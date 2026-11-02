"""Role defaults for background expression learning and its rotation.

Every bound used to be a module constant, so an installation could not widen the
sample window, raise the batch size or give an adopted rule more room without a
code change. They are now one configuration group with one owner.

``rotation_decay_hours`` is the answer to the distribution problem: a rule that
was just injected is de-prioritised for that many hours, so a pool with more
adopted rules than one reply can carry still rotates instead of repeating the
same handful of favourites. ``rotation_candidate_limit`` bounds how many rules a
single turn considers, which keeps the read per turn fixed.
"""

from dataclasses import dataclass
import json

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError, encode
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.foundation.refs import ScopeKind, SubjectKind


GROUP = "expression.learning"
OWNER = "expression.learning"
PRIVATE_GROUP = GROUP + ".private"
PRIVATE_OWNER = PRIVATE_GROUP
GROUP_SCOPE_GROUP = GROUP + ".group"
GROUP_SCOPE_OWNER = GROUP_SCOPE_GROUP
ROLE_SCOPE_GROUP = GROUP + ".role"
ROLE_SCOPE_OWNER = ROLE_SCOPE_GROUP
POLICY_LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE, ConfigLayer.WINDOW)
PRIVATE_LAYERS = (ConfigLayer.RELATIONSHIP,)
GROUP_LAYERS = (ConfigLayer.WINDOW,)
ROLE_SCOPE_LAYERS = (ConfigLayer.ROLE,)
LIMITS = {
    "window_hours": (1, 720),
    "sample_limit": (4, 64),
    "min_samples": (1, 16),
    "group_min_samples": (5, 64),
    "sample_chars": (40, 4000),
    "active_signal_limit": (12, 480),
    "rules_per_turn": (1, 16),
    "payload_chars": (500, 16000),
    "rotation_candidate_limit": (1, 64),
    "rotation_decay_hours": (1, 8760),
    "scan_interval_minutes": (5, 10080),
    "group_scan_interval_minutes": (5, 10080),
    "group_daily_batch_limit": (1, 50),
    "group_new_messages": (5, 80),
}
DEFAULTS = {
    "private_learning_enabled": True,
    "group_learning_enabled": False,
    "window_hours": 720,
    "sample_limit": 60,
    "min_samples": 2,
    "group_min_samples": 20,
    "sample_chars": 800,
    "active_signal_limit": 60,
    "rules_per_turn": 2,
    "payload_chars": 4000,
    "rotation_candidate_limit": 24,
    "rotation_decay_hours": 336,
    "scan_interval_minutes": 90,
    "group_scan_interval_minutes": 180,
    "learning_mode": "balanced",
    "private_application_enabled": True,
    "group_application_enabled": True,
    "sample_review_enabled": False,
    "style_review_enabled": True,
    "group_daily_batch_limit": 6,
    "group_new_messages": 20,
    "group_member_mode": "all",
    "group_member_ids": "",
}
PRIVATE_DEFAULTS = {"private_learning_enabled": DEFAULTS["private_learning_enabled"]}
GROUP_DEFAULTS = {"group_learning_enabled": DEFAULTS["group_learning_enabled"],
                  "group_member_mode": DEFAULTS["group_member_mode"],
                  "group_member_ids": DEFAULTS["group_member_ids"]}
ROLE_SCOPE_DEFAULTS = {
    "private_source_mode": "owner",
    "private_source_subject_ids": "[]",
    "private_application_mode": "all",
    "private_application_subject_ids": "[]",
}
SCOPED_FIELDS = frozenset(("group_member_mode", "group_member_ids"))
ROLE_DEFAULTS = {name: value for name, value in DEFAULTS.items() if name not in SCOPED_FIELDS}
BOOLEAN_FIELDS = ("private_learning_enabled", "group_learning_enabled", "private_application_enabled",
                  "group_application_enabled", "sample_review_enabled", "style_review_enabled")
ROLE_BOOLEAN_FIELDS = tuple(name for name in BOOLEAN_FIELDS if name not in SCOPED_FIELDS)
TEXT_FIELDS = {"learning_mode": 16}
GROUP_TEXT_FIELDS = {"group_member_mode": 16, "group_member_ids": 4000}
INTEGER_FIELDS = tuple(LIMITS)
ROLE_INTEGER_FIELDS = tuple(name for name in INTEGER_FIELDS if name not in SCOPED_FIELDS)
MODES = ("light", "balanced", "aggressive")


def member_ids(value):
    """Member IDs are interpreted only inside the selected exact group scope."""
    if type(value) is not str:
        raise ConfigurationError("expression_settings_invalid", "group member IDs must be text")
    values = value.replace("，", ",").replace("\n", ",").split(",")
    result = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if len(result) > 100 or any(len(item) > 128 or any(ch.isspace() for ch in item) for item in result):
        raise ConfigurationError("expression_settings_invalid", "group member IDs are invalid")
    return result


def configuration_fields():
    """Behavior and sampling defaults shared by the whole installation."""
    definitions = [FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.BOOLEAN, ROLE_DEFAULTS[name],
        layers=POLICY_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER) for name in ROLE_BOOLEAN_FIELDS]
    for name in ROLE_INTEGER_FIELDS:
        minimum, maximum = LIMITS[name]
        definitions.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, ROLE_DEFAULTS[name],
            minimum=minimum, maximum=maximum, layers=POLICY_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    for name, maximum in TEXT_FIELDS.items():
        definitions.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.TEXT, ROLE_DEFAULTS[name],
            maximum_chars=maximum, layers=POLICY_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    return tuple(definitions)


def private_configuration_fields():
    return (FieldDefinition(PRIVATE_GROUP + ".private_learning_enabled", PRIVATE_GROUP, PRIVATE_GROUP,
        ValueKind.BOOLEAN, PRIVATE_DEFAULTS["private_learning_enabled"], layers=PRIVATE_LAYERS,
        apply_mode=ApplyMode.IMMEDIATE, owner_id=PRIVATE_OWNER, core_only=True),)


def group_configuration_fields():
    return (
        FieldDefinition(GROUP_SCOPE_GROUP + ".group_learning_enabled", GROUP_SCOPE_GROUP, GROUP_SCOPE_GROUP,
            ValueKind.BOOLEAN, GROUP_DEFAULTS["group_learning_enabled"], layers=GROUP_LAYERS,
            apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP_SCOPE_OWNER, core_only=True),
        FieldDefinition(GROUP_SCOPE_GROUP + ".group_member_mode", GROUP_SCOPE_GROUP, GROUP_SCOPE_GROUP,
            ValueKind.TEXT, GROUP_DEFAULTS["group_member_mode"], maximum_chars=GROUP_TEXT_FIELDS["group_member_mode"],
            layers=GROUP_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP_SCOPE_OWNER, core_only=True),
        FieldDefinition(GROUP_SCOPE_GROUP + ".group_member_ids", GROUP_SCOPE_GROUP, GROUP_SCOPE_GROUP,
            ValueKind.TEXT, GROUP_DEFAULTS["group_member_ids"], maximum_chars=GROUP_TEXT_FIELDS["group_member_ids"],
            layers=GROUP_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP_SCOPE_OWNER, core_only=True),
    )


def role_scope_configuration_fields():
    """Role-owned selection of private learning sources and application targets."""
    return tuple(FieldDefinition(ROLE_SCOPE_GROUP + "." + name, ROLE_SCOPE_GROUP, ROLE_SCOPE_GROUP,
        ValueKind.TEXT, value, maximum_chars=4000, layers=ROLE_SCOPE_LAYERS,
        apply_mode=ApplyMode.IMMEDIATE, owner_id=ROLE_SCOPE_OWNER, core_only=True)
        for name, value in ROLE_SCOPE_DEFAULTS.items())


def field_names():
    """Registered role-owned definitions used by the runtime reader."""
    return configuration_fields()


def target_for(context=None, *, installation_id=None):
    installation_id = installation_id or getattr(getattr(context, "scope", None), "installation_id", None)
    if not installation_id:
        raise ConfigurationError("expression_settings_scope_invalid", "installation identity is required")
    if context is None:
        return ConfigTarget(installation_id, ConfigLayer.INSTALLATION)
    return ConfigTarget(installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def migrate_legacy_policy_values(runtime):
    """Preserve old role/window policies and split the former group interval."""
    promoted = []
    legacy_interval = GROUP + ".scan_interval_minutes"
    group_interval = GROUP + ".group_scan_interval_minutes"
    target = ConfigTarget(runtime.installation_id, ConfigLayer.INSTALLATION)
    with runtime.transaction() as connection:
        existing = connection.execute("""SELECT field_id FROM configuration_values
            WHERE target_key = ? AND field_id IN (?, ?)""",
            (target.key, legacy_interval, group_interval)).fetchall()
        fields = {row["field_id"] for row in existing}
        if legacy_interval in fields and group_interval not in fields:
            source = connection.execute("""SELECT value_json FROM configuration_values
                WHERE target_key = ? AND field_id = ?""", (target.key, legacy_interval)).fetchone()
            definition = next(field for field in configuration_fields()
                              if field.field_id == group_interval)
            try:
                definition.validate(json.loads(source["value_json"]))
            except (TypeError, ValueError):
                return tuple(promoted)
            target_row = connection.execute("""SELECT revision,updated_at FROM configuration_targets
                WHERE target_key = ?""", (target.key,)).fetchone()
            revision = (target_row["revision"] if target_row else 0) + 1
            updated_at = target_row["updated_at"] if target_row else ""
            connection.execute("INSERT OR IGNORE INTO configuration_targets VALUES (?,?,0,?)",
                (target.key, encode(target.document()), updated_at))
            connection.execute("INSERT INTO configuration_values VALUES (?,?,?,?)",
                (target.key, group_interval, source["value_json"], revision))
            connection.execute("UPDATE configuration_targets SET revision=?,updated_at=? WHERE target_key=?",
                (revision, updated_at, target.key))
            promoted.append(group_interval)
    return tuple(promoted)


def private_target_for(context):
    if (context.scope.kind is not ScopeKind.PRIVATE or context.actor.subject.kind is not SubjectKind.HUMAN
            or context.audience.recipients != (context.actor.subject,)):
        raise ConfigurationError("expression_settings_scope_invalid", "select one identified private participant")
    return ConfigTarget(context.scope.installation_id, ConfigLayer.RELATIONSHIP,
        context.role_binding.role, bot_id=context.scope.bot_id, subject=context.actor.subject)


def group_target_for(context):
    if context.scope.kind is not ScopeKind.GROUP:
        raise ConfigurationError("expression_settings_scope_invalid", "select one group")
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def role_scope_target_for(context):
    if context.role_binding.role.kind is not SubjectKind.ROLE:
        raise ConfigurationError("expression_settings_scope_invalid", "select a bound role")
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def _subject_ids(value):
    if type(value) is not str or len(value) > 4000:
        raise ConfigurationError("expression_settings_invalid", "selected relationship IDs are invalid")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        raise ConfigurationError("expression_settings_invalid", "selected relationship IDs are invalid") from None
    if (type(parsed) is not list or len(parsed) > 100
            or any(type(item) is not str or not item.strip() or len(item) > 256
                   or item != item.strip() for item in parsed)
            or len(set(parsed)) != len(parsed)):
        raise ConfigurationError("expression_settings_invalid", "selected relationship IDs are invalid")
    return tuple(parsed)


@dataclass(frozen=True)
class RoleExpressionScopeSettings:
    private_source_mode: str = "owner"
    private_source_subject_ids: str = "[]"
    private_application_mode: str = "all"
    private_application_subject_ids: str = "[]"

    def __post_init__(self):
        if self.private_source_mode not in ("owner", "selected", "all"):
            raise ConfigurationError("expression_settings_invalid", "unknown private source mode")
        if self.private_application_mode not in ("owner", "selected", "all"):
            raise ConfigurationError("expression_settings_invalid", "unknown private application mode")
        _subject_ids(self.private_source_subject_ids)
        _subject_ids(self.private_application_subject_ids)

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(**{name: values.get(name, default)
                      for name, default in ROLE_SCOPE_DEFAULTS.items()})

    @property
    def selected_sources(self):
        return _subject_ids(self.private_source_subject_ids)

    @property
    def selected_applications(self):
        return _subject_ids(self.private_application_subject_ids)


def read_role_scope_settings(runtime, context):
    from companion_v2.role_profile.configuration import (GROUP as PROFILE_GROUP,
        RoleProfileConfigurationOwner, configuration_fields as profile_fields,
        target_for as profile_target_for)

    snapshot = RoleProfileConfigurationOwner(runtime).read(profile_target_for(context), profile_fields())
    prefix = PROFILE_GROUP + "."
    profile_names = {
        "private_source_mode": "expression_private_source_mode",
        "private_source_subject_ids": "expression_private_source_subject_ids",
        "private_application_mode": "expression_private_application_mode",
        "private_application_subject_ids": "expression_private_application_subject_ids",
    }
    values = {name: snapshot.fields[prefix + profile_name].value
              for name, profile_name in profile_names.items()}
    try:
        return RoleExpressionScopeSettings.from_values(values)
    except ConfigurationError:
        return RoleExpressionScopeSettings()


def main_companion_subject(runtime, context):
    from companion_v2.role_profile.configuration import (GROUP as PROFILE_GROUP,
        RoleProfileConfigurationOwner, configuration_fields as profile_fields,
        target_for as profile_target_for)

    snapshot = RoleProfileConfigurationOwner(runtime).read(profile_target_for(context), profile_fields())
    value = snapshot.fields[PROFILE_GROUP + ".main_companion_subject"].value
    explicit = value.strip() if type(value) is str else ""
    if explicit:
        return explicit
    # 默认来源是"本人"，但单人部署往往从未填写主要陪伴者。角色只有一个私聊
    # 参与者时，他就是事实上的本人；多于一个时不猜，仍要求显式配置，避免把
    # 别人的私聊学进角色表达。
    return _sole_private_participant(runtime, context)


def _sole_private_participant(runtime, context):
    """The role's only admitted private participant, or None when ambiguous."""
    parameters = (context.role_binding.role.subject_id, context.scope.bot_id,
                  context.scope.installation_id)
    filter_sql = """json_extract(context_json, '$.role_binding.role.subject_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.bot_id') = ?
        AND json_extract(context_json, '$.role_binding.scope.installation_id') = ?
        AND json_extract(context_json, '$.audience.scope.kind') = 'private'
        AND json_extract(context_json, '$.actor.subject.subject_id') IS NOT NULL"""
    subjects = set()
    for table in ("conversation_batches", "conversation_native_turns",
                  "conversation_native_observations"):
        try:
            rows = runtime.read_all(f"""SELECT DISTINCT json_extract(context_json,
                '$.actor.subject.subject_id') AS subject FROM {table}
                WHERE {filter_sql} LIMIT 64""", parameters)
        except Exception:
            continue
        subjects.update(row["subject"] for row in rows if row["subject"])
        if len(subjects) > 1:
            return None
    return next(iter(subjects)) if len(subjects) == 1 else None


def source_subject_allowed_for_context(runtime, context):
    if context.scope.kind is not ScopeKind.PRIVATE:
        return False
    settings = read_role_scope_settings(runtime, context)
    return source_subject_allowed(settings, context.actor.subject.subject_id,
                                  main_companion_subject(runtime, context))


def application_subject_allowed_for_context(runtime, context):
    if context.scope.kind is not ScopeKind.PRIVATE:
        return False
    settings = read_role_scope_settings(runtime, context)
    return application_subject_allowed(settings, context.actor.subject.subject_id,
                                       main_companion_subject(runtime, context))


def source_subject_allowed(settings, subject_id, owner_subject):
    if settings.private_source_mode == "all":
        return True
    if settings.private_source_mode == "selected":
        return subject_id in settings.selected_sources
    return bool(owner_subject) and subject_id == owner_subject


def application_subject_allowed(settings, subject_id, owner_subject):
    if settings.private_application_mode == "all":
        return True
    if settings.private_application_mode == "selected":
        return subject_id in settings.selected_applications
    return bool(owner_subject) and subject_id == owner_subject


@dataclass(frozen=True)
class ExpressionLearningSettings:
    private_learning_enabled: bool
    group_learning_enabled: bool
    window_hours: int
    sample_limit: int
    min_samples: int
    group_min_samples: int
    sample_chars: int
    active_signal_limit: int
    rules_per_turn: int
    payload_chars: int
    rotation_candidate_limit: int
    rotation_decay_hours: int
    scan_interval_minutes: int
    group_scan_interval_minutes: int = 180
    learning_mode: str = "balanced"
    private_application_enabled: bool = True
    group_application_enabled: bool = True
    sample_review_enabled: bool = False
    style_review_enabled: bool = True
    group_daily_batch_limit: int = 6
    group_new_messages: int = 20
    group_member_mode: str = "all"
    group_member_ids: str = ""

    def __post_init__(self):
        if any(type(getattr(self, name)) is not bool for name in BOOLEAN_FIELDS):
            raise ConfigurationError("expression_settings_invalid", "expression learning switches must be boolean")
        if self.learning_mode not in MODES or self.group_member_mode not in ("all", "selected"):
            raise ConfigurationError("expression_settings_invalid", "unknown expression learning mode")
        for name, maximum in TEXT_FIELDS.items():
            if type(getattr(self, name)) is not str or len(getattr(self, name)) > maximum:
                raise ConfigurationError("expression_settings_invalid", "expression text setting is invalid")
        member_ids(self.group_member_ids)
        for name, (minimum, maximum) in LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigurationError("expression_settings_invalid",
                                         f"{name} is outside its registered range")
        if self.sample_limit < max(self.min_samples, self.group_min_samples):
            raise ConfigurationError("expression_settings_invalid",
                                     "the sample batch cannot be smaller than either scope's minimum sample count")
        if self.rotation_candidate_limit < self.rules_per_turn:
            raise ConfigurationError("expression_settings_invalid",
                                     "the rotation candidate pool cannot be smaller than one turn's rules")

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(**{name: values.get(name, default) for name, default in DEFAULTS.items()})

    def with_group_learning(self, enabled):
        return ExpressionLearningSettings(**{**self.__dict__, "group_learning_enabled": enabled})

    def applies_to(self, context):
        return self.group_application_enabled if context.scope.kind.value == "group" else self.private_application_enabled

    @property
    def selected_members(self):
        return member_ids(self.group_member_ids) if self.group_member_mode == "selected" else None


def read_settings(runtime, context, *, override=None):
    """Read global policy plus the participant or group-specific learning switch."""
    values = dict(DEFAULTS)
    snapshot = ExpressionConfigurationOwner(runtime).read(target_for(context), field_names())
    values = {key.removeprefix(GROUP + "."): field.value for key, field in snapshot.fields.items()}
    values = {**DEFAULTS, **values}
    if context.scope.kind is ScopeKind.PRIVATE:
        target, fields = private_target_for(context), private_configuration_fields()
    elif context.scope.kind is ScopeKind.GROUP:
        target, fields = group_target_for(context), group_configuration_fields()
    else:
        target, fields = None, ()
    if target is not None:
        local = SparseConfigurationOwner(runtime).read(target, fields)
        local_group = PRIVATE_GROUP if target.layer is ConfigLayer.RELATIONSHIP else GROUP_SCOPE_GROUP
        values.update({key.removeprefix(local_group + "."): field.value
                       for key, field in local.fields.items() if field.explicit})
    if override is not None:
        values.update(override)
    try:
        return ExpressionLearningSettings.from_values(values)
    except ConfigurationError:
        return ExpressionLearningSettings.from_values(DEFAULTS)


class ExpressionConfigurationOwner(SparseConfigurationOwner):
    """Reject the combinations the loader could not honour.

    Individual ranges are already enforced by each ``FieldDefinition``. The two
    cross-field rules cannot be expressed there, and a silently defaulted pair
    would look saved while the effective bounds stayed at the defaults.
    """

    owner_id = OWNER

    @staticmethod
    def _validate_combination(fields, values):
        current = dict(DEFAULTS)
        current.update({field.field_id.removeprefix(GROUP + "."):
                        (values[field.field_id].value if field.field_id in values else field.default)
                        for field in fields})
        try:
            ExpressionLearningSettings.from_values(current)
        except ConfigurationError as error:
            raise ConfigurationError("expression_settings_invalid", str(error)) from error

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        self._validate_combination(fields, result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        # The generic patch path only enforces each field's own range, so the
        # cross-field rules are applied here before anything is persisted.
        defaults = {field.field_id: field.default for field in fields}
        prospective = self.preview_set(target, fields,
            changes={**{key: defaults[key] for key in reset}, **changes})
        self._validate_combination(fields, prospective)
        return super().write(target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at)


class PrivateExpressionConfigurationOwner(SparseConfigurationOwner):
    owner_id = PRIVATE_OWNER


class GroupExpressionConfigurationOwner(SparseConfigurationOwner):
    owner_id = GROUP_SCOPE_OWNER

    @staticmethod
    def _validate(fields, values):
        current = dict(GROUP_DEFAULTS)
        current.update({field.field_id.removeprefix(GROUP_SCOPE_GROUP + "."):
                        (values[field.field_id].value if field.field_id in values else field.default)
                        for field in fields})
        try:
            ExpressionLearningSettings.from_values({**DEFAULTS, **current})
        except ConfigurationError as error:
            raise ConfigurationError("expression_settings_invalid", str(error)) from error

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        self._validate(fields, result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        defaults = {field.field_id: field.default for field in fields}
        prospective = self.preview_set(target, fields,
            changes={**{key: defaults[key] for key in reset}, **changes})
        self._validate(fields, prospective)
        return super().write(target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at)


def local_configuration(context=None, *, target=None):
    """Return the narrow participant/group configuration contract for a reply route."""
    if context is not None:
        if context.scope.kind is ScopeKind.PRIVATE:
            selected_target, fields, group, owner = (private_target_for(context),
                private_configuration_fields(), PRIVATE_GROUP, PrivateExpressionConfigurationOwner)
        elif context.scope.kind is ScopeKind.GROUP:
            selected_target, fields, group, owner = (group_target_for(context),
                group_configuration_fields(), GROUP_SCOPE_GROUP, GroupExpressionConfigurationOwner)
        else:
            raise ConfigurationError("expression_settings_scope_invalid", "select a private person or group")
    elif (target is not None and target.layer is ConfigLayer.WINDOW
            and target.scope is not None and target.scope.kind is ScopeKind.GROUP):
        selected_target, fields, group, owner = (target, group_configuration_fields(),
            GROUP_SCOPE_GROUP, GroupExpressionConfigurationOwner)
    else:
        raise ConfigurationError("expression_settings_scope_invalid", "select a private person or group")
    return selected_target, fields, group, owner
