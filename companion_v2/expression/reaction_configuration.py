"""Installation-wide bounds for reaction lookup, its cache and vector upkeep.

V1 exposed flat ``reaction_expression_*`` keys through plugin configuration.
These settings affect the shared search path, not one conversation, so V2 stores
them at installation scope. The catalog itself is role-owned; group vocabulary
and speaker preferences retain their narrower boundaries.

Every field below has exactly one consumer:

``low_latency_enabled``
    the short-lived lookup cache in ``ReactionLibrary._select``.
``candidate_limit``
    the catalog candidate pool passed to ``CatalogSearch.search`` and the number
    of candidates that may be visually reviewed (V1 default 6).
``embedding_weight`` / ``embedding_score_threshold`` / ``embedding_candidate_limit``
    blend weight, admission gate and vector scan cap of ``CatalogSearch.search``.
``embedding_backfill_enabled`` / ``..._batch_size`` / ``..._interval_seconds``
    ``SemanticService.schedule_reaction_backfill``.
``vision_verify_mode``
    the pre-send visual review gate in ``ReactionLibrary._select``.

``vision_verify_mode`` is a **new** key on purpose: ``storage/semantic.py``
validates its own settings with ``type(value) is not type(DEFAULTS[k])`` and
rejects unknown keys, so turning the existing boolean ``vision_review`` into an
enum would refuse every stored value.  The boolean stays the master switch.

``embedding_weight`` deliberately does not copy V1: its code default (0.55), its
schema default (1.5) and its call-site default (0.7) disagreed
(``docs/V1_EMOTION_REACTION_MIGRATION_AUDIT_20260927.md`` section 六).  V2's
current blend adds the raw cosine with coefficient 1.0, so that is the equivalent
magnitude to start from.
"""

from dataclasses import dataclass
import json

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind


GROUP = "expression.reaction"
OWNER = "expression.reaction"
LAYERS = (ConfigLayer.INSTALLATION,)
VISION_MODES = ("off", "embedding", "always")
DEFAULTS = {
    "low_latency_enabled": True,
    "candidate_limit": 6,
    "embedding_weight": 1.0,
    "embedding_score_threshold": 0.42,
    "embedding_candidate_limit": 1200,
    "embedding_backfill_enabled": True,
    "embedding_backfill_batch_size": 24,
    "embedding_backfill_interval_seconds": 300,
    "vision_verify_mode": "embedding",
}
LIMITS = {
    "candidate_limit": (1, 24),
    "embedding_candidate_limit": (20, 5000),
    "embedding_backfill_batch_size": (1, 100),
    "embedding_backfill_interval_seconds": (0, 86400),
}
WEIGHT_RANGE = (0.05, 3.0)
BOOLEAN_FIELDS = ("low_latency_enabled", "embedding_backfill_enabled")
INTEGER_FIELDS = ("candidate_limit", "embedding_candidate_limit",
                  "embedding_backfill_batch_size", "embedding_backfill_interval_seconds")


def configuration_fields():
    definitions = [FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.BOOLEAN, DEFAULTS[name],
        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER) for name in BOOLEAN_FIELDS]
    for name in INTEGER_FIELDS:
        minimum, maximum = LIMITS[name]
        definitions.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, DEFAULTS[name],
            minimum=minimum, maximum=maximum, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    minimum, maximum = WEIGHT_RANGE
    definitions.append(FieldDefinition(GROUP + ".embedding_weight", GROUP, GROUP, ValueKind.WEIGHT,
        DEFAULTS["embedding_weight"], minimum=minimum, maximum=maximum, layers=LAYERS,
        apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    definitions.append(FieldDefinition(GROUP + ".embedding_score_threshold", GROUP, GROUP, ValueKind.PROBABILITY,
        DEFAULTS["embedding_score_threshold"], minimum=0.0, maximum=1.0, layers=LAYERS,
        apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    definitions.append(FieldDefinition(GROUP + ".vision_verify_mode", GROUP, GROUP, ValueKind.TEXT,
        DEFAULTS["vision_verify_mode"], layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    return tuple(definitions)


def field_names():
    return configuration_fields()


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.INSTALLATION)


@dataclass(frozen=True)
class ReactionSettings:
    low_latency_enabled: bool
    candidate_limit: int
    embedding_weight: float
    embedding_score_threshold: float
    embedding_candidate_limit: int
    embedding_backfill_enabled: bool
    embedding_backfill_batch_size: int
    embedding_backfill_interval_seconds: int
    vision_verify_mode: str

    def __post_init__(self):
        for name, (minimum, maximum) in LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigurationError("reaction_settings_invalid", f"{name} is outside its registered range")
        for name in BOOLEAN_FIELDS:
            if type(getattr(self, name)) is not bool:
                raise ConfigurationError("reaction_settings_invalid", f"{name} must be boolean")
        if type(self.embedding_weight) not in (int, float) or not WEIGHT_RANGE[0] <= self.embedding_weight <= WEIGHT_RANGE[1]:
            raise ConfigurationError("reaction_settings_invalid", "embedding_weight is outside its registered range")
        if type(self.embedding_score_threshold) not in (int, float) or not 0.0 <= self.embedding_score_threshold <= 1.0:
            raise ConfigurationError("reaction_settings_invalid", "embedding_score_threshold must be a fraction")
        if self.vision_verify_mode not in VISION_MODES:
            raise ConfigurationError("reaction_settings_invalid", "vision_verify_mode must be off, embedding or always")

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(**{name: values.get(name, default) for name, default in DEFAULTS.items()})


def read_snapshot(runtime, context, *, override=None):
    """Read installation-wide reaction bounds together with their revision.

    The revision is what reaction caches key on, so directory and configuration
    changes invalidate them without a second read. Legacy window overrides are
    copied into the installation layer during host preparation. A malformed
    stored value never blocks a turn: the defaults are returned instead.
    """
    snapshot = SparseConfigurationOwner(runtime.configuration_store).read(target_for(context), field_names())
    values = {key.removeprefix(GROUP + "."): field.value for key, field in snapshot.fields.items()}
    if override is not None:
        values.update(override)
    try:
        return ReactionSettings.from_values(values), snapshot.revision
    except ConfigurationError:
        return ReactionSettings.from_values(DEFAULTS), snapshot.revision


def read_settings(runtime, context, *, override=None):
    return read_snapshot(runtime, context, override=override)[0]


def migrate_window_values(runtime):
    """Prepare legacy reaction settings before read-only host assembly.

    Existing installation values win. Window rows are retained, and repeating
    preparation does not change the installation's configuration revision.
    """
    from companion_v2.configuration.models import encode

    runtime = runtime.configuration_store
    installation = ConfigTarget(runtime.installation_id, ConfigLayer.INSTALLATION)
    prefix = GROUP + "."
    definitions = {field.field_id: field for field in configuration_fields()}
    with runtime.transaction() as connection:
        current = {row["field_id"] for row in connection.execute(
            "SELECT field_id FROM configuration_values WHERE target_key=? AND field_id LIKE ?",
            (installation.key, prefix + "%"))}
        candidates = {}
        rows = connection.execute("""SELECT t.target_json,t.updated_at,v.field_id,v.value_json
            FROM configuration_targets t JOIN configuration_values v ON v.target_key=t.target_key
            WHERE v.field_id LIKE ? ORDER BY t.updated_at DESC,t.target_key""",
            (prefix + "%",)).fetchall()
        for row in rows:
            if row["field_id"] in current or row["field_id"] in candidates:
                continue
            try:
                target = json.loads(row["target_json"])
            except (TypeError, ValueError):
                continue
            if (target.get("installation_id") != runtime.installation_id
                    or target.get("layer") != ConfigLayer.WINDOW.value):
                continue
            try:
                definitions[row["field_id"]].validate(json.loads(row["value_json"]))
            except (KeyError, TypeError, ValueError):
                continue
            candidates[row["field_id"]] = row
        if not candidates:
            return
        target_row = connection.execute(
            "SELECT revision FROM configuration_targets WHERE target_key=?", (installation.key,)).fetchone()
        revision = (target_row["revision"] if target_row else 0) + 1
        updated_at = max(row["updated_at"] for row in candidates.values())
        connection.execute("INSERT OR IGNORE INTO configuration_targets VALUES (?,?,0,?)",
                           (installation.key, encode(installation.document()), updated_at))
        connection.executemany("INSERT INTO configuration_values VALUES (?,?,?,?)",
            ((installation.key, field_id, row["value_json"], revision)
             for field_id, row in candidates.items()))
        connection.execute("UPDATE configuration_targets SET revision=?,updated_at=? WHERE target_key=?",
                           (revision, updated_at, installation.key))


class ReactionConfigurationOwner(SparseConfigurationOwner):
    """Reject what the registry cannot range-check.

    Individual ranges are already enforced by each ``FieldDefinition``; a TEXT
    field only checks its type there, so an operator typo such as ``alwayss``
    would be stored and would silently switch the review gate. The enum and the
    numeric bounds are re-validated through the same dataclass the read path uses.
    """

    owner_id = OWNER

    @staticmethod
    def _validate_combination(fields, values):
        current = {field.field_id.removeprefix(GROUP + "."):
                   (values[field.field_id].value if field.field_id in values else field.default)
                   for field in fields}
        try:
            ReactionSettings.from_values(current)
        except ConfigurationError as error:
            raise ConfigurationError("reaction_settings_invalid", str(error)) from error

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        self._validate_combination(fields, result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        self._validate_combination(fields, self.preview_set(target, fields, changes=changes))
        return super().write(target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at)
