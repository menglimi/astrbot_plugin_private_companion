"""Role growth defaults and participant-scoped grants."""

import json

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.migrations import promote_values_to_installation
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.identity_policy.repository import PersistentIdentityDirectory


GROUP = "role.growth"
# Skill defaults follow the role; installation values remain a fallback for
# upgrades. The permissions below stay window-scoped because grants are per person.
SKILLS_GROUP = "role.growth.skills"
OWNER = "role.growth.access"
SKILLS_OWNER = "role.growth.skills.settings"
PERMISSIONS = {"read_enabled": "role.growth.read", "proposals_enabled": "role.growth.propose"}
SKILL_LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE)

SKILL_FIELDS = (
    FieldDefinition(SKILLS_GROUP + ".enabled", SKILLS_GROUP, SKILLS_GROUP, ValueKind.BOOLEAN, True,
                    layers=SKILL_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=SKILLS_OWNER),
    FieldDefinition(SKILLS_GROUP + ".custom_skills", SKILLS_GROUP, SKILLS_GROUP, ValueKind.TEXT, "[]",
                    layers=SKILL_LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=SKILLS_OWNER),
    FieldDefinition(SKILLS_GROUP + ".schedule_influence_strength", SKILLS_GROUP, SKILLS_GROUP,
                    ValueKind.INTEGER, 0, layers=SKILL_LAYERS, apply_mode=ApplyMode.IMMEDIATE,
                    owner_id=SKILLS_OWNER, minimum=0, maximum=3),
)
INFLUENCE_NOTES = {
    1: "安排日程时参考当前技能与成长目标的实际进展，避免安排明显超出当前能力的活动。",
    2: "参考当前技能与成长目标安排日程：需要练习的活动安排留出时间，难度与近期进展相称。",
    3: "优先安排与活跃成长目标相关的活动；需要练习的活动留出时间，难度与近期进展相称，并说明与目标的关联。",
}


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.BOOLEAN, False,
        layers=(ConfigLayer.WINDOW,), apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=OWNER)
        for name in PERMISSIONS)


def skill_configuration_fields():
    """The role-shared skill group, registered separately from the permissions."""
    return SKILL_FIELDS


def custom_skills(value):
    """Parse the configured custom skill domains into bounded display literals."""
    if type(value) is not str or not value.strip():
        return ()
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = [item for item in value.replace("，", ",").replace("、", ",").split(",") if item.strip()]
    if not isinstance(parsed, list):
        return ()
    skills = tuple(item.strip() for item in parsed if type(item) is str and item.strip())
    return tuple(item[:24] for item in skills[:8])


def skill_settings(runtime, context):
    """Read one role's skill defaults, inheriting older installation values."""
    snapshot = SkillConfigurationOwner(runtime).read(skill_target_for(context), SKILL_FIELDS)
    if snapshot.fields[SKILLS_GROUP + ".enabled"].value is not True:
        return (), 0
    strength = snapshot.fields[SKILLS_GROUP + ".schedule_influence_strength"].value
    return custom_skills(snapshot.fields[SKILLS_GROUP + ".custom_skills"].value), (
        strength if type(strength) is int else 0)


def skills_enabled(runtime, context):
    snapshot = SkillConfigurationOwner(runtime).read(skill_target_for(context), SKILL_FIELDS)
    return snapshot.fields[SKILLS_GROUP + ".enabled"].value is True


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)


def skill_target_for(context):
    """Role preferences remain stable across that role's conversations."""
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def migrate_legacy_skill_values(runtime):
    """Keep old window values as an installation fallback without globalizing roles."""
    return promote_values_to_installation(runtime, SKILL_FIELDS,
                                          source_layers=(ConfigLayer.WINDOW,))


class SkillConfigurationOwner(SparseConfigurationOwner):
    """Role skill preferences with a read-only installation fallback."""
    owner_id = SKILLS_OWNER


class GrowthConfigurationOwner(SparseConfigurationOwner):
    """Every entry point, including generic configuration patches, updates grants."""

    owner_id = OWNER
    writes_main_runtime = True

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction(), self.store.transaction():
            if target.scope.kind is not ScopeKind.PRIVATE:
                raise ConfigurationError("growth_target_changed", "select a private participant")
            route = PersonaDirectory(self.runtime).resolve(target.scope, observed_at=observed_at)
            if not route.is_success or route.unwrap().binding.role != target.role:
                raise ConfigurationError("growth_target_changed", "the selected private role changed")
            participant = route.unwrap().route.user
            if participant is None or participant.kind is not SubjectKind.HUMAN:
                raise ConfigurationError("growth_target_changed", "select a private participant")
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            identity = PersistentIdentityDirectory(self.runtime)
            for name, action in PERMISSIONS.items():
                field = GROUP + "." + name
                if field not in changes and field not in reset:
                    continue
                prefix = "growth-grant:" + digest([target.key, participant, action]) + ":"
                for grant in identity.snapshot().grants:
                    if grant.grant_id.startswith(prefix) and grant.state is GrantState.ACTIVE:
                        identity.revoke_grant(grant.grant_id, revoked_at=observed_at,
                                              expected_revision=identity.revision)
                if changes.get(field, False):
                    identity.issue_grant(grant_id=prefix + str(identity.revision), subject=participant,
                        scope=target.scope, capability=action, tier=AuthorizationTier.STANDARD,
                        valid_from=observed_at, expected_revision=identity.revision)
            return result
