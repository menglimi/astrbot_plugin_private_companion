"""Role-wide switches and throttles for the QQ Zone extension.

This group answers only two questions: whether the role may act publicly on its
own Zone, and how many public actions one turn may produce. It never holds an
account credential and it never proves the link is ready -- a real read or post
still passes capability installation, role enablement, per-conversation consent,
host credentials and the generation check.
"""

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require

from .topics import QzoneTopic, TOPIC_SPECS, allowed_scenes


GROUP = "qzone"
OWNER = "qzone.settings"

DEFAULTS = {
    # Master switch: off means no Zone call at all, and no model spend on one.
    "enabled": False,
    # How far back one feed scan reads.
    "feed_recent_posts": 10,
    # The three public actions stay off until an operator turns them on. Being on
    # is still not consent: every single action is authorized again at call time.
    "like_enabled": False,
    "comment_enabled": False,
    "publish_enabled": False,
    # Public-action spacing and the per-turn ceiling.
    "action_min_interval_minutes": 30,
    "max_actions_per_tick": 1,
    # How many of the role's own posts a comment reply may scan.
    "comment_recent_posts": 5,
    # Daily ceiling for self-published posts. The wording of a post is a separate
    # decision; this is only the safety cap.
    "publish_max_daily": 1,
    # Topic classification is model guided.  The explicit confirmation gate
    # keeps an automatically drafted public post in a reviewable state.
    "topic_auto_enabled": False,
    "topic_require_confirmation": True,
    "topic_prompt_guidance": "",
    # V1 的「生活说说」：低频率、按概率把角色自己的生活发到空间。它不替代
    # 上面的 publish_enabled（总闸），也不绕过 policy 的间隔/每日上限/去重。
    "life_publish_enabled": False,
    "life_publish_probability": 0.18,
    "life_publish_min_interval_hours": 24,
    "life_publish_max_daily": 1,
    "life_publish_style_prompt": "",
}
for _topic in TOPIC_SPECS:
    _key = _topic.topic.value
    DEFAULTS.update({
        f"{_key}_enabled": True,
        f"{_key}_daily_max": 1,
        f"{_key}_cooldown_minutes": 30,
        f"{_key}_min_confidence": 0.65,
        f"{_key}_scenes": ",".join(scene.value for scene in _topic.scenes),
    })
LIMITS = {
    "feed_recent_posts": (1, 30),
    "action_min_interval_minutes": (5, 1440),
    "max_actions_per_tick": (1, 5),
    "comment_recent_posts": (1, 20),
    "publish_max_daily": (1, 10),
    "life_publish_min_interval_hours": (1, 168),
    "life_publish_max_daily": (1, 10),
}
for _topic in TOPIC_SPECS:
    _key = _topic.topic.value
    LIMITS.update({
        f"{_key}_daily_max": (0, 10),
        f"{_key}_cooldown_minutes": (0, 1440),
    })
BOOLEAN_FIELDS = ("enabled", "like_enabled", "comment_enabled", "publish_enabled",
                  "topic_auto_enabled", "topic_require_confirmation", "life_publish_enabled") + tuple(
                      f"{item.topic.value}_enabled" for item in TOPIC_SPECS)
INTEGER_FIELDS = tuple(LIMITS)
PROBABILITY_FIELDS = ("life_publish_probability",) + tuple(
    f"{item.topic.value}_min_confidence" for item in TOPIC_SPECS)
TEXT_FIELDS = ("topic_prompt_guidance", "life_publish_style_prompt") + tuple(
    f"{item.topic.value}_scenes" for item in TOPIC_SPECS)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("qzone_configuration_invalid", "provide the declared QQ Zone settings")
    if any(type(values[name]) is not bool for name in BOOLEAN_FIELDS):
        raise ConfigurationError("qzone_configuration_invalid", "QQ Zone switches must be boolean")
    for name in INTEGER_FIELDS:
        low, high = LIMITS[name]
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("qzone_configuration_invalid", f"{name} must be an integer from {low} to {high}")
    for name in PROBABILITY_FIELDS:
        value = values[name]
        if (isinstance(value, bool) or type(value) not in (int, float) or not 0 <= value <= 1):
            raise ConfigurationError("qzone_configuration_invalid", f"{name} must be a confidence from 0 to 1")
    if type(values["topic_prompt_guidance"]) is not str or len(values["topic_prompt_guidance"]) > 4000:
        raise ConfigurationError("qzone_configuration_invalid", "topic_prompt_guidance is too long")
    if type(values["life_publish_style_prompt"]) is not str or len(values["life_publish_style_prompt"]) > 512:
        raise ConfigurationError("qzone_configuration_invalid", "life_publish_style_prompt is too long")
    for item in TOPIC_SPECS:
        name = f"{item.topic.value}_scenes"
        try:
            configured = {scene.value for scene in allowed_scenes(values[name])}
        except (TypeError, ValueError):
            raise ConfigurationError("qzone_configuration_invalid", f"{name} contains an unsupported scene") from None
        allowed = {scene.value for scene in item.scenes}
        if not configured or not configured.issubset(allowed):
            raise ConfigurationError("qzone_configuration_invalid", f"{name} must use scenes allowed for its topic")
    return values


def configuration_fields():
    fields = []
    for name, value in DEFAULTS.items():
        if type(value) is bool:
            kind = ValueKind.BOOLEAN
        elif type(value) is int:
            kind = ValueKind.INTEGER
        elif type(value) is float:
            kind = ValueKind.PROBABILITY
        else:
            kind = ValueKind.TEXT
        options = {"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}
        if name in PROBABILITY_FIELDS:
            options["decimal_places"] = 3
        if name == "topic_prompt_guidance":
            options["maximum_chars"] = 4000
        elif name in TEXT_FIELDS:
            options["maximum_chars"] = 512
        fields.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, kind, value,
            layers=(ConfigLayer.WINDOW, ConfigLayer.ROLE), apply_mode=ApplyMode.IMMEDIATE,
            owner_id=OWNER, **options))
    return tuple(fields)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


class QzoneConfigurationOwner(RoleSharedSettingsOwner):
    """The Zone account belongs to the role, so every field is role-shared.

    A window may still hold its own pre-existing value as the legacy fallback;
    that is exactly what ``RoleSharedSettingsOwner`` already does for other
    role-wide groups, so no extra rule is invented here.
    """

    owner_id = OWNER

    def is_shared(self, field):
        return field.group_id == GROUP

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        validate({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction():
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            validate({key.removeprefix(GROUP + "."): item.value
                      for key, item in self.read(target, fields).fields.items()})
            return result


class QzoneSettings:
    """Read one role's Zone options without inventing a second scope."""

    def __init__(self, configuration):
        self.configuration = configuration

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                   group_id=GROUP, observed_at=at))
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["BOOLEAN_FIELDS", "DEFAULTS", "GROUP", "INTEGER_FIELDS", "LIMITS", "OWNER",
           "QzoneConfigurationOwner", "QzoneSettings", "configuration_fields", "target_for", "validate"]
