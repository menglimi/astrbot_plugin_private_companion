"""Reading a page the participant linked, fixed per window.

这一组只决定「读不读、读几条、读多长、等多久」。真正的安全边界在读取侧：
只允许 http(s)、逐跳校验地址、拒绝内网；这里不提供任何放宽那些限制的开关。
"""

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "conversation.web"
OWNER = GROUP
DEFAULTS = {
    "enabled": True,
    "max_links": 2,
    "max_chars": 4000,
    "timeout_seconds": 8,
}
LIMITS = {"max_links": (1, 5), "max_chars": (200, 8000), "timeout_seconds": (3, 30)}


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("web_understanding_invalid", "provide the declared web reading settings")
    if type(values["enabled"]) is not bool:
        raise ConfigurationError("web_understanding_invalid", "web reading switch must be boolean")
    for name, (low, high) in LIMITS.items():
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("web_understanding_invalid",
                                     f"{name} must be an integer from {low} to {high}")
    return values


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER, value,
        layers=(ConfigLayer.WINDOW,), owner_id=OWNER,
        **({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}))
        for name, value in DEFAULTS.items())


class WebUnderstandingConfigurationOwner(SparseConfigurationOwner):
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


class WebUnderstandingSettings:
    def __init__(self, configuration):
        self.configuration = configuration

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                   group_id=GROUP, observed_at=at))
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["DEFAULTS", "GROUP", "LIMITS", "OWNER", "WebUnderstandingConfigurationOwner",
           "WebUnderstandingSettings", "configuration_fields", "target_for", "validate"]
