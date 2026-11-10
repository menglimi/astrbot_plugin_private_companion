"""Role defaults and per-person learning consent for acquaintance cards."""

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.foundation.refs import ScopeKind, SubjectKind


GROUP = "person"
LEARNING_GROUP = "person.learning"


def configuration_fields():
    return (
        FieldDefinition("person.acquaintance_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP),
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.acquaintance_card_chars", GROUP, "person", ValueKind.INTEGER, 1024,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=256, maximum=4096,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_awareness_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_continuity_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_continuity_minutes", GROUP, "person", ValueKind.INTEGER, 30,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=5, maximum=180,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_private_group_hours", GROUP, "person", ValueKind.INTEGER, 12,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=1, maximum=48,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_max_items", GROUP, "person", ValueKind.INTEGER, 4,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=1, maximum=8,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_attention_minutes", GROUP, "person", ValueKind.INTEGER, 30,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=0, maximum=120,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_attention_wait_seconds", GROUP, "person", ValueKind.INTEGER, 3,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=0, maximum=10,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_group_identity_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_peer_minutes", GROUP, "person", ValueKind.INTEGER, 5,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=1, maximum=60,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.cross_window_peer_reply_minutes", GROUP, "person", ValueKind.INTEGER, 60,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=1, maximum=720,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.private_group_context_mode", GROUP, "person", ValueKind.TEXT, "base",
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), maximum_chars=24,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.private_group_context_minutes", GROUP, "person", ValueKind.INTEGER, 120,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=5, maximum=1440,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.private_group_context_chars", GROUP, "person", ValueKind.INTEGER, 600,
                        layers=(ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP), minimum=100, maximum=2000,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
    )


def target_for(context):
    return relationship_target_for(context)


def role_target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role,
                        bot_id=context.scope.bot_id)


def relationship_target_for(context):
    if context.actor.subject.kind is not SubjectKind.HUMAN:
        raise ValueError("acquaintance configuration requires one human subject")
    return ConfigTarget(context.scope.installation_id, ConfigLayer.RELATIONSHIP,
                        context.role_binding.role, bot_id=context.scope.bot_id,
                        subject=context.actor.subject)


def learning_configuration_fields():
    return (FieldDefinition(
        "person.learning.acquaintance_learning_enabled", LEARNING_GROUP, "person", ValueKind.BOOLEAN, True,
        layers=(ConfigLayer.RELATIONSHIP,), apply_mode=ApplyMode.IMMEDIATE, core_only=True),)


def legacy_window_target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def legacy_window_fields():
    """Unregistered read contract for values saved before person settings were split."""
    return (
        FieldDefinition("person.acquaintance_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.WINDOW,), apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.acquaintance_learning_enabled", GROUP, "person", ValueKind.BOOLEAN, True,
                        layers=(ConfigLayer.WINDOW,), apply_mode=ApplyMode.IMMEDIATE, core_only=True),
        FieldDefinition("person.acquaintance_card_chars", GROUP, "person", ValueKind.INTEGER, 1024,
                        layers=(ConfigLayer.WINDOW,), minimum=256, maximum=4096,
                        apply_mode=ApplyMode.IMMEDIATE, core_only=True),
    )


def legacy_window_snapshot(runtime, context):
    return SparseConfigurationOwner(runtime).read(legacy_window_target_for(context), legacy_window_fields())


class AcquaintanceSettings:
    def __init__(self, configuration):
        self.configuration = configuration
        self.owner = SparseConfigurationOwner(configuration.runtime)
        self.definitions = configuration.registry.group(GROUP)
        self.learning_definitions = configuration.registry.group(LEARNING_GROUP)

    def read(self, context):
        with self.configuration.runtime.read_snapshot():
            snapshot = self.owner.read(target_for(context), self.definitions)
            learning = self.owner.read(target_for(context), self.learning_definitions)
            settings = {key: item.value for key, item in snapshot.fields.items()}
            settings.update({key: item.value for key, item in learning.fields.items()})
            if context.scope.kind is ScopeKind.PRIVATE and context.audience.recipients == (context.actor.subject,):
                legacy = legacy_window_snapshot(self.configuration.runtime, context)
                mapping = {
                    "person.acquaintance_enabled": "person.acquaintance_enabled",
                    "person.acquaintance_card_chars": "person.acquaintance_card_chars",
                    "person.learning.acquaintance_learning_enabled": "person.acquaintance_learning_enabled",
                }
                current = {**snapshot.fields, **learning.fields}
                for field_id, legacy_id in mapping.items():
                    if not current[field_id].explicit and legacy.fields[legacy_id].explicit:
                        settings[field_id] = legacy.fields[legacy_id].value
            return settings
