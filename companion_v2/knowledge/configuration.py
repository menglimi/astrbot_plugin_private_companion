"""Operator-visible bounds for knowledge import, injection and member clues.

V1 exposed twelve settings for its relation graph.  V2 keeps the ones with a real
consumer, on the V2 side of the same boundary:

===============================  ===============================================
V1 key                           V2 field
===============================  ===============================================
``worldbook_auto_import``        ``knowledge.import_enabled``
``worldbook_config_paths``       ``knowledge.import_paths``
``roleplay_knowledge_source_ids`` ``knowledge.source_ids``
``enable_worldbook_member_recognition`` ``knowledge.member_recognition_enabled``
``worldbook_member_match_aliases`` ``knowledge.member_match_aliases``
``worldbook_member_inject_limit`` ``knowledge.member_inject_limit``
===============================  ===============================================

Everything else is a bound V2 needs in order to stay honest and bounded
(per-entry characters, per-source entries, per-snapshot sources and characters,
per-turn entries and characters, member clue characters).  The V1 self
registration keys are intentionally not registered -- see the slice document:
they would write member profiles, which V2 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind


GROUP = "knowledge"
OWNER = "knowledge"
LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE, ConfigLayer.WINDOW)
BOOLEAN_FIELDS = (
    "import_enabled",
    "inject_enabled",
    "member_recognition_enabled",
    "member_match_aliases",
)
DEFAULTS = {
    "import_enabled": False,
    "import_paths": "",
    "source_ids": "",
    "entry_limit": 500,
    "entry_char_limit": 1200,
    "source_limit": 16,
    "candidate_char_budget": 60000,
    "inject_enabled": True,
    "inject_max_entries": 8,
    "inject_max_chars": 6000,
    "member_recognition_enabled": False,
    "member_match_aliases": True,
    "member_inject_limit": 6,
    "member_clue_chars": 1200,
}
LIMITS = {
    "entry_limit": (1, 5000),
    "entry_char_limit": (64, 4000),
    "source_limit": (1, 64),
    "candidate_char_budget": (2000, 200000),
    "inject_max_entries": (1, 24),
    "inject_max_chars": (500, 24000),
    "member_inject_limit": (1, 12),
    "member_clue_chars": (200, 4000),
}
TEXT_FIELDS = ("import_paths", "source_ids")
TEXT_LIMIT = 20000
SEPARATORS = re.compile(r"[\r\n,;，；]+")


def configuration_fields():
    definitions = [
        FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.BOOLEAN, DEFAULTS[name],
                        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=OWNER)
        for name in BOOLEAN_FIELDS]
    definitions.extend(
        FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.TEXT, DEFAULTS[name],
                        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=OWNER)
        for name in TEXT_FIELDS)
    for name, (minimum, maximum) in LIMITS.items():
        definitions.append(
            FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, DEFAULTS[name],
                            minimum=minimum, maximum=maximum, layers=LAYERS,
                            apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=OWNER))
    return tuple(definitions)


def field_names():
    """Registered definitions for one window read; the sparse owner needs them."""
    return configuration_fields()


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                        context.scope)


@dataclass(frozen=True)
class KnowledgeSettings:
    import_enabled: bool
    import_paths: str
    source_ids: str
    entry_limit: int
    entry_char_limit: int
    source_limit: int
    candidate_char_budget: int
    inject_enabled: bool
    inject_max_entries: int
    inject_max_chars: int
    member_recognition_enabled: bool
    member_match_aliases: bool
    member_inject_limit: int
    member_clue_chars: int

    def __post_init__(self) -> None:
        for name in BOOLEAN_FIELDS:
            if type(getattr(self, name)) is not bool:
                raise ConfigurationError("knowledge_settings_invalid",
                                         f"{name} must be an explicit boolean")
        for name in TEXT_FIELDS:
            value = getattr(self, name)
            if type(value) is not str or len(value) > TEXT_LIMIT:
                raise ConfigurationError("knowledge_settings_invalid",
                                         f"{name} must be bounded text")
        for name, (minimum, maximum) in LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ConfigurationError("knowledge_settings_invalid",
                                         f"{name} is outside its registered range")
        if self.inject_max_chars < self.entry_char_limit:
            # One stored entry could then never fit the turn budget, which would
            # look saved while silently injecting nothing.
            raise ConfigurationError("knowledge_settings_invalid",
                                     "the knowledge character budget cannot be smaller than one entry")

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(**{name: values.get(name, default) for name, default in DEFAULTS.items()})

    def paths(self):
        return _configured_list(self.import_paths, limit=self.source_limit)

    def knowledge_source_ids(self):
        return _configured_list(self.source_ids, limit=self.source_limit)


def _configured_list(value, *, limit):
    """Parse one operator text field into at most ``source_limit`` entries."""
    if type(limit) is not int or limit < 1:
        raise ValueError("configured list needs a positive bound")
    items = []
    for part in SEPARATORS.split(str(value or "")):
        text = part.strip()
        if not text or len(text) > 260:
            continue
        if text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return tuple(items)


def read_settings(runtime, context, *, override=None):
    """Read this window's effective bounds; malformed stored values never block a turn."""
    return _read(runtime, target_for(context), override=override)


def read_installation_settings(runtime, installation_id, *, override=None):
    """Installation-layer bounds for host work that runs outside a conversation.

    A start-up refresh has no window, so only installation-layer values apply and
    every role or window override stays untouched for the turns that read them.
    """
    return _read(runtime, ConfigTarget(installation_id, ConfigLayer.INSTALLATION), override=override)


def _read(runtime, target, *, override=None):
    snapshot = SparseConfigurationOwner(runtime.configuration_store).read(target, field_names())
    values = {key.removeprefix(GROUP + "."): field.value for key, field in snapshot.fields.items()}
    if override is not None:
        values.update(override)
    try:
        return KnowledgeSettings.from_values(values)
    except ConfigurationError:
        return KnowledgeSettings.from_values(DEFAULTS)


class KnowledgeConfigurationOwner(SparseConfigurationOwner):
    """Reject the pair the field ranges cannot express.

    Each range is enforced by its own ``FieldDefinition``.  The cross-field rule
    (a turn budget smaller than one stored entry) cannot be, and a silently
    defaulted pair would look saved while nothing was ever injected.
    """

    owner_id = OWNER

    @staticmethod
    def _validate_combination(fields, values):
        current = {field.field_id.removeprefix(GROUP + "."):
                   (values[field.field_id].value if field.field_id in values else field.default)
                   for field in fields}
        try:
            KnowledgeSettings.from_values(current)
        except ConfigurationError as error:
            raise ConfigurationError("knowledge_settings_invalid", str(error)) from error

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        self._validate_combination(fields, result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        self._validate_combination(fields, self.preview_set(target, fields, changes=changes))
        return super().write(target, fields, changes=changes, reset=reset,
                             expected_revision=expected_revision, observed_at=observed_at)


__all__ = [
    "BOOLEAN_FIELDS", "DEFAULTS", "GROUP", "KnowledgeConfigurationOwner", "KnowledgeSettings",
    "LAYERS", "LIMITS", "OWNER", "TEXT_FIELDS", "configuration_fields", "field_names",
    "read_installation_settings", "read_settings", "target_for",
]
