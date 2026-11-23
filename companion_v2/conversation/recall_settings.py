"""Withdrawn-message transcription options, fixed per window.

V1 kept a short-lived message cache purely so a withdrawn message could still be
transcribed. V2 already stores every admitted input and marks withdrawal in place,
so this group only decides whether that management read is allowed, how far back
it looks and how much text it returns. It never reactivates a withdrawn source and
never re-enters generation.
"""

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "conversation.recall"
OWNER = GROUP
DEFAULTS = {
    # 默认开启：撤回感知本身就是"看得到撤回"，用户不需要先去某个页面
    # 找到开关。真正的边界在读取侧——只有核心管理员（memory.manage，
    # core_only=True）能调用转写，普通聊天不会因此多出任何上下文。
    "transcribe_enabled": True,
    "transcribe_max_items": 5,
    "transcribe_text_chars": 500,
    "transcribe_window_minutes": 10,
}
LIMITS = {"transcribe_max_items": (1, 20), "transcribe_text_chars": (80, 2000),
          "transcribe_window_minutes": (1, 1440)}


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("recall_configuration_invalid", "provide the declared recall settings")
    if type(values["transcribe_enabled"]) is not bool:
        raise ConfigurationError("recall_configuration_invalid", "recall switches must be boolean")
    for name, (low, high) in LIMITS.items():
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("recall_configuration_invalid", f"{name} must be an integer from {low} to {high}")
    return values


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER, value,
        layers=(ConfigLayer.WINDOW,), owner_id=OWNER,
        **({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}))
        for name, value in DEFAULTS.items())


class RecallConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        validate({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.store.transaction():
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            validate({key.removeprefix(GROUP + "."): item.value
                      for key, item in self.read(target, fields).fields.items()})
            return result


class RecallSettings:
    def __init__(self, configuration):
        self.configuration = configuration

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                   group_id=GROUP, observed_at=at))
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["DEFAULTS", "GROUP", "LIMITS", "OWNER", "RecallConfigurationOwner", "RecallSettings",
           "configuration_fields", "target_for", "validate"]
