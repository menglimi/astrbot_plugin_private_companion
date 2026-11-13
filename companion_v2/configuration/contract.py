"""The read-side half of the config contract every editor publishes.

``GroupEditor.status`` and the reply editor's ``expression_status`` both answer
the dashboard with the same three extras beyond the plain values: the
inheritance chain the write lands in, where each effective value actually came
from, and the registered bounds the page renders controls from.

Keeping that in one place matters because the page draws its layer rail from
these three and offers "reset to inherit" from ``explicit``. A second copy that
drifts would show a field as locally set when it is inherited, and saving would
write a redundant copy of a value the role layer already owns.
"""

from functools import lru_cache

from companion_v2.configuration.registry import ValueKind


def layers_of(target):
    """The inheritance chain this target writes into, outermost first."""
    result = []
    for step in target.chain():
        name = step.layer.value
        if name not in result:
            result.append(name)
    return result


@lru_cache(maxsize=128)
def definitions_of(registry, group):
    """Registered field definitions for one group, keyed by short name.

    The registry is immutable for the life of a host generation, so rebuilding
    this mapping (and the contract derived from it) on every status read only
    spent CPU on the 170+ field contact group.
    """
    prefix = group + "."
    return {field.field_id.removeprefix(prefix): field
            for field in registry.group(group) if field.field_id.startswith(prefix)}


def provenance_of(snapshot, group):
    """Per field: which layer the effective value came from, and whether it is local.

    ``explicit`` is true only when the edited layer owns the value. When it is
    false the value is inherited, so the page offers "reset to inherit" rather
    than writing a redundant copy.
    """
    prefix = group + "."
    result = {}
    for key, effective in snapshot.fields.items():
        if not key.startswith(prefix):
            continue
        source = effective.source
        result[key.removeprefix(prefix)] = {
            "layer": source.layer.value if source is not None else None,
            "explicit": bool(effective.explicit),
            "revision": effective.source_revision,
        }
    return result


@lru_cache(maxsize=128)
def contract_of(registry, group):
    """Bounds and kinds the workbench renders controls from.

    Mirrors the registered ``FieldDefinition`` so a page never hard-codes a
    range that can silently drift away from the validator.
    """
    result = {}
    for name, field in definitions_of(registry, group).items():
        entry = {"kind": field.kind.value, "default": field.default,
                 "apply_mode": field.apply_mode.value,
                 "editable_layers": [layer.value for layer in field.layers]}
        if field.minimum is not None:
            entry["minimum"] = field.minimum
        if field.maximum is not None:
            entry["maximum"] = field.maximum
        if field.maximum_chars is not None:
            entry["maximum_chars"] = field.maximum_chars
        if field.kind in (ValueKind.PROBABILITY, ValueKind.WEIGHT) and field.decimal_places is not None:
            entry["decimal_places"] = field.decimal_places
        result[name] = entry
    return result
