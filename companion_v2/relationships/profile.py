"""Operator-confirmed, per-person relationship context for one role."""

from __future__ import annotations

from companion_v2.configuration.models import (ApplyMode, ConfigLayer, ConfigTarget,
                                               ConfigurationError)
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.system_prompts.catalog import RELATIONSHIP_INSTRUCTION as INSTRUCTION
from companion_v2.system_prompts.context import SystemPromptContext


GROUP = "relationship.profile"
OWNER = "relationship.profile.settings"
TOTAL_CHAR_LIMIT = 3000
FIELD_LIMITS = {
    "default_nickname": 100,
    "user_gender": 80,
    "user_birthday": 100,
    "user_occupation": 160,
    "role_relation": 240,
    "interaction": 900,
    "extra": 900,
}
FIELD_LABELS = {
    "default_nickname": "对用户的称呼",
    "user_gender": "用户性别",
    "user_birthday": "用户生日",
    "user_occupation": "用户职业",
    "role_relation": "用户与角色的关系",
    "interaction": "相处方式",
    "extra": "其他补充",
}


def configuration_fields():
    return tuple(
        FieldDefinition(
            f"{GROUP}.{name}", GROUP, GROUP, ValueKind.TEXT, "",
            layers=(ConfigLayer.RELATIONSHIP,), apply_mode=ApplyMode.IMMEDIATE,
            maximum_chars=limit, owner_id=OWNER, core_only=True,
        )
        for name, limit in FIELD_LIMITS.items()
    )


def target_for(context):
    if (context.scope.kind is not ScopeKind.PRIVATE
            or context.audience.recipients != (context.actor.subject,)
            or context.actor.subject.kind is not SubjectKind.HUMAN):
        raise TaskError("relationship_profile_scope_invalid",
                        "select one identified person in a private conversation")
    return ConfigTarget(
        context.scope.installation_id, ConfigLayer.RELATIONSHIP,
        context.role_binding.role, bot_id=context.scope.bot_id,
        subject=context.actor.subject,
    )


def validate_values(values):
    if type(values) is not dict or set(values) != set(FIELD_LIMITS):
        raise ConfigurationError("relationship_profile_configuration_invalid",
                                  "relationship profile fields are incomplete")
    total = 0
    for name, limit in FIELD_LIMITS.items():
        value = values[name]
        if type(value) is not str or len(value) > limit:
            raise ConfigurationError("relationship_profile_configuration_invalid",
                                      f"{name} must be text no longer than {limit} characters")
        total += len(value)
    if total > TOTAL_CHAR_LIMIT:
        raise ConfigurationError("relationship_profile_configuration_invalid",
                                  f"the complete relationship profile must be no longer than {TOTAL_CHAR_LIMIT} characters")
    return values


class RelationshipProfileConfigurationOwner(SparseConfigurationOwner):
    """Keep per-person profile writes within the same bounded contract."""

    owner_id = OWNER

    @staticmethod
    def _values(snapshot, fields):
        prefix = GROUP + "."
        return {
            field.field_id.removeprefix(prefix): snapshot.fields[field.field_id].value
            for field in fields
        }

    def read(self, target, fields):
        snapshot = super().read(target, fields)
        validate_values(self._values(snapshot, fields))
        return snapshot

    def preview_set(self, target, fields, *, changes):
        current = super().read(target, fields)
        values = self._values(current, fields)
        prefix = GROUP + "."
        values.update({name.removeprefix(prefix): value for name, value in changes.items()})
        validate_values(values)
        return super().preview_set(target, fields, changes=changes)

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        current = super().read(target, fields)
        values = self._values(current, fields)
        prefix = GROUP + "."
        values.update({name.removeprefix(prefix): value for name, value in changes.items()})
        for name in reset:
            values[name.removeprefix(prefix)] = ""
        validate_values(values)
        return super().write(
            target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at,
        )


class ConversationRelationshipProfileContext:
    def __init__(self, runtime: StorageRuntime):
        self.owner = RelationshipProfileConfigurationOwner(runtime)
        self.fields = configuration_fields()

    def capture(self, context, at):
        del at
        try:
            target = target_for(context)
        except TaskError:
            return None
        snapshot = self.owner.read(target, self.fields)
        values = {
            name: snapshot.fields[f"{GROUP}.{name}"].value
            for name in FIELD_LIMITS
        }
        if not any(value.strip() for value in values.values()):
            return None
        return {
            "role_id": context.role_binding.role.subject_id,
            "subject_id": context.actor.subject.subject_id,
            "context_key": context.key,
            "revision": snapshot.revision,
            "fields": values,
        }

    def instruction(self, snapshot, context):
        if snapshot is None:
            return ""
        required = {"role_id", "subject_id", "context_key", "revision", "fields"}
        if (type(snapshot) is not dict or set(snapshot) != required
                or snapshot["role_id"] != context.role_binding.role.subject_id
                or snapshot["subject_id"] != context.actor.subject.subject_id
                or snapshot["context_key"] != context.key
                or type(snapshot["revision"]) is not int or snapshot["revision"] < 0):
            raise TaskError("relationship_profile_snapshot_invalid",
                            "the sealed relationship profile does not match this person")
        try:
            values = validate_values(snapshot["fields"])
        except (ConfigurationError, TypeError, ValueError) as error:
            raise TaskError("relationship_profile_snapshot_invalid",
                            "the sealed relationship profile is invalid") from error
        lines = [f"- {FIELD_LABELS[name]}：{value.strip()}"
                 for name, value in values.items() if value.strip()]
        instruction = INSTRUCTION
        try:
            instruction = SystemPromptContext(self.runtime).overrides(context).get("relationship") or INSTRUCTION
        except Exception:
            pass
        return "\n\n【当前私聊关系资料】\n" + instruction + "\n" + "\n".join(lines) if lines else ""


__all__ = ["FIELD_LABELS", "FIELD_LIMITS", "GROUP", "INSTRUCTION", "OWNER",
           "TOTAL_CHAR_LIMIT", "ConversationRelationshipProfileContext",
           "RelationshipProfileConfigurationOwner", "configuration_fields",
           "target_for", "validate_values"]
