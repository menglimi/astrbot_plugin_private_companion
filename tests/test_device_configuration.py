"""The role-wide phone group is the runtime source; phone.json is only a bootstrap."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
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
                values = {field.field_id: field.default for field in fields}
                values[GROUP + ".enabled"] = True
                values[GROUP + ".unicode_input"] = "adbkeyboard"
                return Outcome.success(SimpleNamespace(values=values))

        context = SimpleNamespace(actor=object(), role_binding=SimpleNamespace(role=ROLE),
                                  scope=SimpleNamespace(installation_id="install", bot_id="bot-a"))
        settings = PhoneSettingsReader(FakeConfiguration()).read(context, NOW)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.unicode_input, "adbkeyboard")

    def test_phone_json_wins_only_until_the_group_has_an_explicit_value(self):
        from companion_v2.configuration.models import EffectiveValue

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
                return Outcome.success(SimpleNamespace(values=values))

        context = SimpleNamespace(actor=object(), role_binding=SimpleNamespace(role=ROLE),
                                  scope=SimpleNamespace(installation_id="install", bot_id="bot-a"))
        self.assertTrue(PhoneSettingsReader(FakeConfiguration(False), fallback_root=root)
                        .read(context, NOW).enabled)
        self.assertFalse(PhoneSettingsReader(FakeConfiguration(True), fallback_root=root)
                         .read(context, NOW).enabled)


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
