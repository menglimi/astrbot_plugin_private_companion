"""Unified state and platform-presentation options with exact private grants."""

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from .cycle import DEFAULTS as CYCLE_DEFAULTS, validate_settings
from .humanized import DEFAULTS as HUMANIZED_DEFAULTS, LIMITS as HUMANIZED_LIMITS
from .humanized import validate_settings as validate_humanized
from .settings_scope import PRESENCE_FIELDS, PhysicalSettingsOwner, role_preference
from companion_v2.agenda.planning_config import DEFAULTS as PLANNING_DEFAULTS, MAXIMUMS as PLANNING_MAXIMUMS
from companion_v2.agenda.planning_config import WEATHER_MAX_AGE_MINIMUM
from companion_v2.agenda.planning_config import validate_settings as validate_planning
from companion_v2.agenda.calendar_access import revoke_role_calendar_grants

GROUP = "role.state"
OWNER = "role.state.settings"
FEATURE_SWITCHES = {name: name + "_enabled" for name in (
    "energy", "health", "hunger", "sleep", "cycle", "mood", "busy", "location")}
SPECIAL_DISPLAY_FEATURES = ("health", "hunger", "sleep", "cycle", "busy")
DEFAULT_GRANT_SCOPE = "role"
GRANT_SCOPES = frozenset({"window", "role"})
DEFAULTS = {"enabled": False, "maintenance_grant_scope": DEFAULT_GRANT_SCOPE,
    "maintenance_minutes": 30, "hunger_enabled": True,
    "health_enabled": True, "cycle_enabled": False, "calendar_enabled": True,
    "energy_enabled": True, "sleep_enabled": True, "busy_enabled": True, "location_enabled": True,
    "energy_expression_prompt": "", "hunger_expression_prompt": "", "health_expression_prompt": "",
    "home_show_energy": True, "home_show_health": True, "home_show_hunger": True,
    "home_show_sleep": True, "home_show_cycle": True, "home_show_busy": True,
    "home_show_location": True, "home_show_mood": True,
    **{"home_only_special_" + name: False for name in SPECIAL_DISPLAY_FEATURES},
    # Refresh without a stop/start toggle. Four seconds reduces request frequency;
    # actual expiry and flicker depend on the QQ client and need field validation.
    "typing_enabled": False, "typing_refresh_seconds": 4, "presence_enabled": False,
    "online_status_enabled": False,
    # Bounded mood dynamics. The probability shape the update, the half-life shapes the
    # regression, and the step cap keeps one message from swinging an axis to an extreme.
    # Retained for stored-config compatibility; role simulation is always persona-wide.
    "mood_enabled": False, "mood_cross_window": True, "mood_update_probability": 0.6,
    "mood_interest_window_minutes": 120, "mood_update_max_delta": 2,
    "mood_decay_half_life_minutes": 90, "mood_neutral_band": 3,
    # Bounded humanised expression plus the V1 daily-review cadence/retention.
    **HUMANIZED_DEFAULTS, **CYCLE_DEFAULTS, **PLANNING_DEFAULTS}
MOOD_LIMITS = {"mood_interest_window_minutes": (5, 1440), "mood_update_max_delta": (1, 4),
    "mood_decay_half_life_minutes": (5, 1440), "mood_neutral_band": (1, 4)}
PERMISSIONS = {"enabled": ("role.state.read", "role.state.maintain"),
    "typing_enabled": ("platform.typing",), "presence_enabled": ("platform.presence",),
    "online_status_enabled": ("platform.presence",)}
EXPRESSION_PROMPTS = ("energy_expression_prompt", "hunger_expression_prompt", "health_expression_prompt")


def validate_expression_prompts(values):
    for name in EXPRESSION_PROMPTS:
        value = values.get(name, "")
        if type(value) is not str or len(value) > 512:
            raise ConfigurationError("state_expression_prompt_invalid", f"{name} must be text up to 512 characters")


def validate_grant_scope(values):
    scope = values.get("maintenance_grant_scope", DEFAULT_GRANT_SCOPE)
    if scope not in GRANT_SCOPES:
        raise ConfigurationError("state_grant_scope_invalid",
            "maintenance_grant_scope must be 'window' or 'role'")


def validate_mood_settings(values):
    """Mood bounds are closed here too, so a page or direct write cannot widen them."""
    for name, (low, high) in MOOD_LIMITS.items():
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("state_mood_settings", f"{name} must be an integer from {low} to {high}")
    probability = values["mood_update_probability"]
    if type(probability) not in (int, float) or not 0 <= probability <= 1:
        raise ConfigurationError("state_mood_settings", "mood_update_probability must be a fraction from 0 to 1")
    for name in ("mood_enabled", "mood_cross_window"):
        if type(values[name]) is not bool:
            raise ConfigurationError("state_mood_settings", f"{name} must be a boolean")


def sync_window_grants(runtime, target, participant, settings, changed, observed_at):
    if target.layer is not ConfigLayer.WINDOW or participant is None:
        return
    identity = PersistentIdentityDirectory(runtime)
    for action in {action for actions in PERMISSIONS.values() for action in actions}:
        options = [name for name, actions in PERMISSIONS.items() if action in actions]
        if not changed.intersection(options):
            continue
        prefix = "state-grant:" + digest([target.key, participant, action]) + ":"
        for grant in identity.snapshot().grants:
            if grant.grant_id.startswith(prefix) and grant.state is GrantState.ACTIVE:
                identity.revoke_grant(grant.grant_id, revoked_at=observed_at,
                                      expected_revision=identity.revision)
        if any(settings[name] for name in options):
            identity.issue_grant(grant_id=prefix + str(identity.revision), subject=participant,
                scope=target.scope, capability=action, tier=AuthorizationTier.STANDARD,
                valid_from=observed_at, expected_revision=identity.revision)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)


def migrate_presence_to_role(runtime):
    """Promote legacy window-scoped QQ presence switches to role defaults."""
    from companion_v2.configuration.migrations import promote_values_to_role
    fields = tuple(field for field in configuration_fields()
                   if field.field_id.rsplit(".", 1)[-1] in PRESENCE_FIELDS)
    return promote_values_to_role(runtime, fields)


def ensure_window_presentation_grants(runtime, context, observed_at):
    """Issue missing grants for a private window after role-level presence changes."""
    if context.scope.kind is not ScopeKind.PRIVATE:
        return
    target = target_for(context)
    fields = configuration_fields()
    snapshot = StateConfigurationOwner(runtime).read(target, fields)
    settings = {name: snapshot.fields[GROUP + "." + name].value for name in DEFAULTS}
    identity = PersistentIdentityDirectory(runtime)
    subject = context.actor.subject
    by_action = {}
    for option in ("typing_enabled", "presence_enabled", "online_status_enabled"):
        for action in PERMISSIONS[option]:
            by_action.setdefault(action, set()).add(option)
    for action, options in by_action.items():
        prefix = "state-grant:" + digest([target.key, subject, action]) + ":"
        matching = tuple(grant for grant in identity.grants_for_subject(subject)
                         if grant.grant_id.startswith(prefix) and grant.scope == target.scope)
        active = tuple(grant for grant in matching if grant.state is GrantState.ACTIVE
                       and grant.valid_from <= observed_at
                       and (grant.expires_at is None or grant.expires_at > observed_at))
        if not any(settings[name] for name in options):
            for grant in active:
                identity.revoke_grant(grant.grant_id, revoked_at=observed_at,
                                      expected_revision=identity.revision)
            continue
        if matching:
            continue
        identity.issue_grant(grant_id=prefix + str(identity.revision), subject=subject,
            scope=target.scope, capability=action, tier=AuthorizationTier.STANDARD,
            valid_from=observed_at, expected_revision=identity.revision)


def ensure_role_state_grants(runtime, context, observed_at):
    """Issue the role-wide state grants for this private window.

    The default ``role`` mode treats one private window's opt-in as the role's
    opt-in and walks other windows automatically.  The stricter ``window`` mode
    keeps the grant tied to the window where the user enabled or re-saved
    maintenance, so one participant cannot authorize another participant's
    private window.  Existing grants and explicit revocations are left
    untouched in either mode.
    """
    if context.scope.kind is not ScopeKind.PRIVATE:
        return
    target = target_for(context)
    fields = configuration_fields()
    snapshot = StateConfigurationOwner(runtime).read(target, fields)
    if snapshot.fields[GROUP + ".maintenance_grant_scope"].value != "role":
        return
    if not bool(snapshot.fields[GROUP + ".enabled"].value):
        return
    identity = PersistentIdentityDirectory(runtime)
    subject = context.actor.subject
    for action in PERMISSIONS["enabled"]:
        prefix = "state-grant:" + digest([target.key, subject, action]) + ":"
        matching = tuple(grant for grant in identity.grants_for_subject(subject)
                         if grant.grant_id.startswith(prefix) and grant.scope == target.scope)
        if matching:
            continue
        identity.issue_grant(grant_id=prefix + str(identity.revision), subject=subject,
            scope=target.scope, capability=action, tier=AuthorizationTier.STANDARD,
            valid_from=observed_at, expected_revision=identity.revision)


def role_target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role,
                        bot_id=context.scope.bot_id)


def configuration_fields():
    fields = []
    for name, value in DEFAULTS.items():
        kind = (ValueKind.BOOLEAN if type(value) is bool else ValueKind.PROBABILITY if name == "mood_update_probability"
                else ValueKind.INTEGER if type(value) is int else ValueKind.TEXT)
        bounds = {}
        if name in MOOD_LIMITS:
            bounds = {"minimum": MOOD_LIMITS[name][0], "maximum": MOOD_LIMITS[name][1]}
        elif name in HUMANIZED_LIMITS:
            bounds = {"minimum": HUMANIZED_LIMITS[name][0], "maximum": HUMANIZED_LIMITS[name][1]}
        elif name == "planning_weather_max_age_minutes":
            # Bounded here too, so the registry cannot advertise a value validate_settings rejects.
            bounds = {"minimum": WEATHER_MAX_AGE_MINIMUM, "maximum": PLANNING_MAXIMUMS[name]}
        elif name == "cycle_discomfort_chance":
            bounds = {"minimum": 0, "maximum": 100}
        elif kind is ValueKind.INTEGER:
            phase_energy = name.startswith("cycle_") and name.endswith("_energy")
            bounds = {"minimum": -50 if phase_energy else 1,
                      "maximum": PLANNING_MAXIMUMS.get(name, 30 if name.startswith("cycle_")
                                                       else 10 if name == "typing_refresh_seconds" else 1440)}
        fields.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, kind, value,
            layers=(ConfigLayer.ROLE, ConfigLayer.WINDOW), apply_mode=ApplyMode.IMMEDIATE,
            core_only=True, owner_id=OWNER, **bounds))
    return tuple(fields)


class StateConfigurationOwner(PhysicalSettingsOwner):
    owner_id = OWNER

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        values = {key.rsplit(".", 1)[-1]: field.value for key, field in result.items()}
        validate_expression_prompts(values)
        validate_settings(values)
        validate_planning(values)
        validate_mood_settings(values)
        validate_humanized(values)
        validate_grant_scope(values)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction():
            names = {key.rsplit(".", 1)[-1] for key in (*changes, *reset)}
            if target.layer is ConfigLayer.ROLE and any(not role_preference(name) for name in names):
                raise ConfigurationError("state_configuration_scope_not_supported",
                    "typing presentation settings belong to the selected window")
            participant = None
            if target.layer is ConfigLayer.WINDOW:
                route = PersonaDirectory(self.runtime).resolve(target.scope, observed_at=observed_at)
                if (target.scope.kind is not ScopeKind.PRIVATE or not route.is_success
                        or route.unwrap().binding.role != target.role or route.unwrap().route.user is None):
                    raise ConfigurationError("state_target_changed", "select the current private role and participant")
                participant = route.unwrap().route.user
            proposed = {key.rsplit(".", 1)[-1]: field.value for key, field in self.read(target, fields).fields.items()}
            proposed.update({key.rsplit(".", 1)[-1]: value for key, value in changes.items()})
            proposed.update({key.rsplit(".", 1)[-1]: DEFAULTS[key.rsplit(".", 1)[-1]] for key in reset})
            validate_settings(proposed)
            validate_expression_prompts(proposed)
            validate_planning(proposed)
            validate_mood_settings(proposed)
            validate_humanized(proposed)
            validate_grant_scope(proposed)
            saved = super().write(target, fields, changes=changes, reset=reset,
                                  expected_revision=expected_revision, observed_at=observed_at)
            changed = {key.rsplit(".", 1)[-1] for key in (*changes, *reset)}
            if "calendar_enabled" in changed and not proposed["calendar_enabled"]:
                identity = PersistentIdentityDirectory(self.runtime)
                revoke_role_calendar_grants(identity, installation_id=target.installation_id,
                    bot_id=target.bot_id, role=target.role, revoked_at=observed_at)
            sync_window_grants(self.runtime, target, participant, proposed, changed, observed_at)
            return saved
