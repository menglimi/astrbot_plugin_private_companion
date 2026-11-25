"""The role-wide phone group is the runtime source; phone.json is only a bootstrap."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue
from companion_v2.device.configuration import (
    GROUP, PhoneConfigurationOwner, PhoneSettingsReader, configuration_fields, load_settings,
    settings_from_values, validate,
)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.storage.runtime import StorageRuntime

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
ROLE = SubjectRef("install", SubjectKind.BOT, "persona-a")


class PhoneConfigurationTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(prefix="phone-config-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = Path(root) / "data"
        data.mkdir()
        self.runtime = StorageRuntime.open(data_root=data, installation_id="install")
        self.addCleanup(self.runtime.close)
        self.fields = configuration_fields()
        self.owner = PhoneConfigurationOwner(self.runtime)
        self.target = ConfigTarget("install", ConfigLayer.ROLE, ROLE, bot_id="bot-a")

    def test_defaults_are_disabled_and_readable(self):
        snapshot = self.owner.read(self.target, self.fields)
        values = {key.removeprefix(GROUP + "."): item.value for key, item in snapshot.fields.items()}
        self.assertFalse(values["enabled"])
        self.assertEqual(values["max_steps"], 4)
        self.assertEqual(values["allowed_actions"], "launch,tap_text,tap,swipe,type,key,wait_text,done,fail")

    def test_a_write_round_trips_and_parses_into_settings(self):
        changes = {
            GROUP + ".enabled": True,
            GROUP + ".adb_path": "C:/Android/platform-tools/adb.exe",
            GROUP + ".serial": "192.168.1.23:37123",
            GROUP + ".unicode_input": "adbkeyboard",
            GROUP + ".allowed_actions": "launch,tap_text,type,done,fail",
            GROUP + ".allowed_subjects": "u-1",
        }
        self.owner.write(self.target, self.fields, changes=changes, reset=(),
                         expected_revision=0, observed_at=NOW)
        snapshot = self.owner.read(self.target, self.fields)
        values = {key.removeprefix(GROUP + "."): item.value for key, item in snapshot.fields.items()}
        settings = settings_from_values(values)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.allowed, ("launch", "tap_text", "type", "done", "fail"))
        self.assertEqual(settings.allowed_subjects, ("u-1",))
        self.assertTrue(settings.permits(SimpleNamespace(actor=SimpleNamespace(
            subject=SimpleNamespace(subject_id="u-1")))))

    def test_bad_allowlists_are_rejected_before_save(self):
        base = {key.removeprefix(GROUP + "."): value for key, value in (
            (field.field_id, field.default) for field in self.fields)}
        with self.assertRaises(ConfigurationError):
            validate({**base, "allowed_actions": "launch"})  # done/fail missing
        with self.assertRaises(ConfigurationError):
            validate({**base, "allowed_actions": "launch,shell,done,fail"})
        with self.assertRaises(ConfigurationError):
            validate({**base, "unicode_input": "ime"})

    def test_reader_uses_the_role_target_and_returns_settings(self):
        fields = self.fields

        class FakeConfiguration:
            def read(self, actor, *, target, group_id, observed_at):
                values = {field.field_id: EffectiveValue(field.default, None, False) for field in fields}
                values[GROUP + ".enabled"] = EffectiveValue(True, target, True, 1)
                values[GROUP + ".unicode_input"] = EffectiveValue("adbkeyboard", target, True, 1)
                return Outcome.success(SimpleNamespace(fields=values))

        context = SimpleNamespace(actor=object(), role_binding=SimpleNamespace(role=ROLE),
                                  scope=SimpleNamespace(installation_id="install", bot_id="bot-a"))
        settings = PhoneSettingsReader(FakeConfiguration()).read(context, NOW)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.unicode_input, "adbkeyboard")

    def test_phone_json_wins_only_until_the_group_has_an_explicit_value(self):
        fields = self.fields
        root = tempfile.mkdtemp(prefix="phone-fallback-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (Path(root) / "phone.json").write_text(json.dumps({
            "enabled": True, "allowed": ["launch", "done", "fail"]}), encoding="utf-8")

        class FakeConfiguration:
            def __init__(self, explicit):
                self.explicit = explicit

            def read(self, actor, *, target, group_id, observed_at):
                values = {field.field_id: EffectiveValue(field.default, None, False, 0)
                          for field in fields}
                if self.explicit:
                    values[GROUP + ".enabled"] = EffectiveValue(False, target, True, 1)
                return Outcome.success(SimpleNamespace(fields=values))

        context = SimpleNamespace(actor=object(), role_binding=SimpleNamespace(role=ROLE),
                                  scope=SimpleNamespace(installation_id="install", bot_id="bot-a"))
        self.assertTrue(PhoneSettingsReader(FakeConfiguration(False), fallback_root=root)
                        .read(context, NOW).enabled)
        self.assertFalse(PhoneSettingsReader(FakeConfiguration(True), fallback_root=root)
                         .read(context, NOW).enabled)

    def test_verified_aliases_fill_missing_fields_without_resurrecting_a_reset(self):
        alias = replace(self.target, role=replace(ROLE, subject_id="旧人格"))
        other_bot = replace(alias, bot_id="other-bot")
        adb_path = GROUP + ".adb_path"
        enabled = GROUP + ".enabled"
        path = "E:/Android/platform-tools/adb.exe"
        changes = {adb_path: path, enabled: True, GROUP + ".connect": "127.0.0.1:37345"}
        for target in (alias, other_bot):
            self.owner.write(target, self.fields, changes=changes, reset=(), expected_revision=0, observed_at=NOW)
        self.owner.write(self.target, self.fields, changes={enabled: False}, reset=(),
                         expected_revision=0, observed_at=NOW)
        aliases = {(self.target.bot_id, alias.role.subject_id): ROLE.subject_id}
        self.owner.merge_role_aliases(aliases, NOW)
        effective = self.owner.read(self.target, self.fields)
        self.assertFalse(effective.fields[enabled].value)
        self.assertEqual(effective.fields[adb_path].value, path)
        self.assertEqual(effective.fields[GROUP + ".connect"].value, "127.0.0.1:37345")
        self.assertIsNone(self.owner.read(alias, self.fields).fields[adb_path].source)
        self.assertEqual(self.owner.read(other_bot, self.fields).fields[adb_path].value, path)

        self.owner.write(self.target, self.fields, changes={}, reset=(adb_path,),
                         expected_revision=effective.revision, observed_at=NOW)
        self.owner.merge_role_aliases(aliases, NOW)
        self.assertEqual(self.owner.read(self.target, self.fields).fields[adb_path].value, "")


class PhoneJsonBootstrapTests(unittest.TestCase):
    def test_json_is_only_a_fallback_reader(self):
        root = tempfile.mkdtemp(prefix="phone-json-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (Path(root) / "phone.json").write_text(json.dumps({
            "enabled": True, "allowed": ["launch", "done", "fail"]}), encoding="utf-8")
        settings = load_settings(root)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.allowed, ("launch", "done", "fail"))


if __name__ == "__main__":
    unittest.main()
