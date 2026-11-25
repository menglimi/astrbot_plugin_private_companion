"""Role-wide ADB device settings, editable from the cottage.

The phone is a physical resource, but every autonomous step already runs under
a private window's role binding.  Keeping this group role-shared means the
background service can read it with the task's own context instead of inventing
a dashboard actor, and a different role can point at a different device without
splitting one role's behavior across windows.

``phone.json`` remains a bootstrap fallback: an installation that has never
saved this group keeps working, and the page writes the group from then on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
from pathlib import Path

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.storage.codec import encode_timestamp

from .adb import AdbClient, discover_adb

GROUP = "device.phone"
OWNER = "device.phone.settings"
SETTINGS_FILE = "phone.json"
DEFAULT_MAX_STEPS = 4
MAX_STEPS = 20
UNICODE_INPUTS = ("off", "adbkeyboard")
DEFAULT_ALLOWED = "launch,tap_text,tap,swipe,type,key,wait_text,done,fail"

DEFAULTS = {
    "enabled": False,
    "adb_path": "",
    "serial": "",
    "connect": "",
    "max_steps": DEFAULT_MAX_STEPS,
    "unicode_input": "off",
    "ime_restore_seconds": 30,
    "allowed_actions": DEFAULT_ALLOWED,
    "allowed_subjects": "",
}
LIMITS = {"max_steps": (1, MAX_STEPS), "ime_restore_seconds": (5, 120)}
TEXT_LIMITS = {"adb_path": 4096, "serial": 256, "connect": 256,
               "allowed_actions": 512, "allowed_subjects": 1024, "unicode_input": 16}


def _actions(value: str) -> tuple[str, ...]:
    from .session import ACTION_KINDS

    parts = tuple(part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip())
    if (not parts or len(set(parts)) != len(parts)
            or any(part not in ACTION_KINDS for part in parts)
            or not {"done", "fail"} <= set(parts)):
        raise ConfigurationError("device_phone_configuration_invalid",
                                 "allowed_actions must list declared actions once, including done and fail")
    return parts


def _subjects(value: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip())
    if len(set(parts)) != len(parts) or any(len(part) > 128 for part in parts):
        raise ConfigurationError("device_phone_configuration_invalid",
                                 "allowed_subjects must list unique short subject ids")
    return parts


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("device_phone_configuration_invalid", "provide the declared phone settings")
    if type(values["enabled"]) is not bool:
        raise ConfigurationError("device_phone_configuration_invalid", "enabled must be boolean")
    for name, maximum in TEXT_LIMITS.items():
        value = values[name]
        if type(value) is not str or len(value) > maximum:
            raise ConfigurationError("device_phone_configuration_invalid", f"{name} must be a bounded string")
    if values["unicode_input"] not in UNICODE_INPUTS:
        raise ConfigurationError("device_phone_configuration_invalid", "unicode_input must be off or adbkeyboard")
    for name, (low, high) in LIMITS.items():
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("device_phone_configuration_invalid",
                                     f"{name} must be an integer from {low} to {high}")
    _actions(values["allowed_actions"])
    _subjects(values["allowed_subjects"])
    return values


def configuration_fields():
    fields = []
    for name, value in DEFAULTS.items():
        if type(value) is bool:
            kind = ValueKind.BOOLEAN
        elif type(value) is int:
            kind = ValueKind.INTEGER
        else:
            kind = ValueKind.TEXT
        options = {}
        if name in LIMITS:
            options.update(minimum=LIMITS[name][0], maximum=LIMITS[name][1])
        if name in TEXT_LIMITS:
            options["maximum_chars"] = TEXT_LIMITS[name]
        fields.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, kind, value,
            layers=(ConfigLayer.ROLE,), apply_mode=ApplyMode.IMMEDIATE,
            owner_id=OWNER, **options))
    return tuple(fields)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def settings_from_values(values) -> "PhoneSettings":
    validate(values)
    return PhoneSettings(
        enabled=values["enabled"], adb=values["adb_path"], serial=values["serial"],
        connect=values["connect"], max_steps=values["max_steps"],
        allowed=_actions(values["allowed_actions"]),
        allowed_subjects=_subjects(values["allowed_subjects"]),
        unicode_input=values["unicode_input"],
        ime_restore_seconds=values["ime_restore_seconds"],
    )


@dataclass(frozen=True)
class PhoneSettings:
    enabled: bool = False
    adb: str = ""
    serial: str = ""
    connect: str = ""
    max_steps: int = DEFAULT_MAX_STEPS
    allowed: tuple[str, ...] = tuple(DEFAULT_ALLOWED.split(","))
    allowed_subjects: tuple[str, ...] = ()
    unicode_input: str = "off"
    ime_restore_seconds: int = 30

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("phone settings enabled must be boolean")
        for name in ("adb", "serial", "connect"):
            value = getattr(self, name)
            if type(value) is not str or len(value) > 4096:
                raise ValueError(f"phone settings {name} must be a short string")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= MAX_STEPS:
            raise ValueError(f"phone settings max_steps must be between 1 and {MAX_STEPS}")
        if (not self.allowed or len(set(self.allowed)) != len(self.allowed)
                or not {"done", "fail"} <= set(self.allowed)):
            raise ValueError("phone settings allowed must name declared actions")
        if (type(self.allowed_subjects) is not tuple
                or any(type(item) is not str or not item.strip() or len(item) > 128
                       for item in self.allowed_subjects)
                or len(set(self.allowed_subjects)) != len(self.allowed_subjects)):
            raise ValueError("phone settings allowed_subjects must be unique short subject ids")
        if self.unicode_input not in UNICODE_INPUTS:
            raise ValueError("phone settings unicode_input must be off or adbkeyboard")
        if type(self.ime_restore_seconds) is not int or not 5 <= self.ime_restore_seconds <= 120:
            raise ValueError("phone settings ime_restore_seconds must be between 5 and 120")

    def permits(self, context) -> bool:
        if not self.allowed_subjects:
            return True
        try:
            return context.actor.subject.subject_id in self.allowed_subjects
        except AttributeError:
            return False

    def client(self) -> AdbClient:
        executable = discover_adb(self.adb or None)
        if self.connect:
            AdbClient(executable).connect(self.connect)
        return AdbClient(executable, serial=self.serial or None, timeout=20.0)


class PhoneConfigurationOwner(RoleSharedSettingsOwner):
    owner_id = OWNER

    def is_shared(self, field):
        return field.group_id == GROUP

    def merge_role_aliases(self, aliases, observed_at):
        """Consolidate phone values for aliases verified by the host's persona map."""
        fields = configuration_fields()
        groups = {}
        for (bot_id, alias), canonical in aliases.items():
            destination = ConfigTarget(self.runtime.installation_id, ConfigLayer.ROLE,
                SubjectRef(self.runtime.installation_id, SubjectKind.BOT, canonical), bot_id=bot_id)
            source = replace(destination, role=replace(destination.role, subject_id=alias))
            if source != destination:
                groups.setdefault(destination, []).append(source.key)
        if not groups:
            return

        timestamp = encode_timestamp(observed_at)
        with self.store.transaction() as connection:
            for destination, source_keys in groups.items():
                placeholders = ",".join("?" for _ in source_keys)
                rows = connection.execute(f"""SELECT v.target_key,v.field_id,v.value_json
                    FROM configuration_values v JOIN configuration_targets t USING(target_key)
                    WHERE v.target_key IN ({placeholders}) AND v.field_id LIKE ?
                    ORDER BY t.updated_at DESC,t.target_key""", (*source_keys, GROUP + ".%")).fetchall()
                if not rows:
                    continue
                current = self.read(destination, fields)
                changes, moved = {}, {}
                for row in rows:
                    field_id = row["field_id"]
                    if field_id not in current.fields:
                        continue
                    try:
                        value = json.loads(row["value_json"])
                        validate({**DEFAULTS, field_id.removeprefix(GROUP + "."): value})
                    except (TypeError, ValueError):
                        continue
                    if not current.fields[field_id].explicit:
                        changes.setdefault(field_id, value)
                    moved.setdefault(row["target_key"], []).append(field_id)
                if changes:
                    self.write(destination, fields, changes=changes, reset=(),
                               expected_revision=current.revision, observed_at=observed_at)
                for source_key, field_ids in moved.items():
                    connection.executemany("DELETE FROM configuration_values WHERE target_key=? AND field_id=?",
                                           ((source_key, field_id) for field_id in field_ids))
                    # Keep the target row when existing change receipts or other groups need it.
                    connection.execute("""DELETE FROM configuration_targets WHERE target_key=?
                        AND NOT EXISTS (SELECT 1 FROM configuration_values WHERE target_key=?)
                        AND NOT EXISTS (SELECT 1 FROM configuration_changes WHERE target_key=?)""",
                        (source_key, source_key, source_key))
                    connection.execute("UPDATE configuration_targets SET revision=revision+1,updated_at=? WHERE target_key=?",
                                       (timestamp, source_key))

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


class PhoneSettingsReader:
    """Read one role's phone settings through the original private context.

    ``fallback_root`` keeps an installation that already has ``phone.json``
    working until the page saves the group once: a snapshot whose phone fields
    all come from defaults has no explicit source, so the bootstrap file wins.
    After the first saved value the configuration is authoritative.
    """

    def __init__(self, configuration, *, fallback_root: str | Path | None = None):
        self.configuration = configuration
        self.fallback_root = Path(fallback_root) if fallback_root is not None else None
        self.role_aliases = {}

    def adopt_role_aliases(self, aliases, observed_at):
        if aliases:
            PhoneConfigurationOwner(self.configuration.runtime).merge_role_aliases(aliases, observed_at)
        self.role_aliases = dict(aliases)

    def target_for(self, context):
        target = target_for(context)
        canonical = self.role_aliases.get((target.bot_id, target.role.subject_id))
        return replace(target, role=replace(target.role, subject_id=canonical)) if canonical else target

    def read(self, context, at):
        return self.read_for(context.actor, self.target_for(context), at)

    def read_for(self, actor, target, at):
        settings, _ = self.read_with_source(actor, target, at)
        return settings

    def read_with_source(self, actor, target, at):
        snapshot = require(self.configuration.read(actor, target=target, group_id=GROUP, observed_at=at))
        if (self.fallback_root is not None and (self.fallback_root / SETTINGS_FILE).is_file()
                and all(field.source is None for field in snapshot.fields.values())):
            try:
                return load_settings(self.fallback_root), SETTINGS_FILE
            except ValueError:
                pass
        values = {key.removeprefix(GROUP + "."): field.value for key, field in snapshot.fields.items()}
        source = snapshot.fields[GROUP + ".adb_path"].source
        return settings_from_values(values), "role_config" if source is not None else "default"


def load_settings(root: str | Path) -> PhoneSettings:
    """Bootstrap file, used only when no configuration service is attached."""
    path = Path(root) / SETTINGS_FILE
    if not path.is_file():
        return PhoneSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("phone.json is not readable JSON") from error
    if type(payload) is not dict:
        raise ValueError("phone.json must contain one object")
    allowed_keys = {"enabled", "adb", "serial", "connect", "max_steps", "allowed", "allowed_subjects",
                    "unicode_input", "ime_restore_seconds"}
    unknown = set(payload) - allowed_keys
    if unknown:
        raise ValueError("phone.json contains unknown fields: " + ", ".join(sorted(unknown)))
    allowed = payload.get("allowed", list(DEFAULT_ALLOWED.split(",")))
    if type(allowed) is not list or any(type(item) is not str for item in allowed):
        raise ValueError("phone.json allowed must be a list of action names")
    subjects = payload.get("allowed_subjects", [])
    if type(subjects) is not list or any(type(item) is not str for item in subjects):
        raise ValueError("phone.json allowed_subjects must be a list of subject ids")
    return PhoneSettings(
        enabled=payload.get("enabled", False),
        adb=str(payload.get("adb") or ""),
        serial=str(payload.get("serial") or ""),
        connect=str(payload.get("connect") or ""),
        max_steps=payload.get("max_steps", MAX_STEPS),
        allowed=tuple(allowed),
        allowed_subjects=tuple(subjects),
        unicode_input=payload.get("unicode_input", "off"),
        ime_restore_seconds=payload.get("ime_restore_seconds", 30),
    )


__all__ = ["DEFAULTS", "GROUP", "OWNER", "PhoneConfigurationOwner", "PhoneSettings",
           "PhoneSettingsReader", "configuration_fields", "load_settings", "settings_from_values",
           "target_for", "validate"]
