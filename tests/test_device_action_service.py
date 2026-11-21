"""Phone task records are the durable state; nothing here starts ADB or a model."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from companion_v2.device.action_service import (
    PhoneActionService, PhoneSettings, PhoneTaskRecord, load_settings,
)
from companion_v2.storage.runtime import StorageRuntime


class PhoneSettingsTests(unittest.TestCase):
    def test_missing_file_means_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(load_settings(root).enabled)

    def test_subject_allowlist_can_restrict_the_channel(self):
        from types import SimpleNamespace

        settings = PhoneSettings(enabled=True, allowed_subjects=("u-1",))
        context = SimpleNamespace(actor=SimpleNamespace(subject=SimpleNamespace(subject_id="u-1")))
        other = SimpleNamespace(actor=SimpleNamespace(subject=SimpleNamespace(subject_id="u-2")))
        self.assertTrue(settings.permits(context))
        self.assertFalse(settings.permits(other))
        self.assertTrue(PhoneSettings(enabled=True).permits(other))

    def test_explicit_enable_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "phone.json"
            path.write_text(json.dumps({"enabled": True, "serial": "A1", "max_steps": 3}),
                            encoding="utf-8")
            settings = load_settings(root)
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.serial, "A1")
            self.assertEqual(settings.max_steps, 3)
            path.write_text(json.dumps({"enabled": True, "evil": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_invalid_enabled_type_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text('{"enabled": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_done_and_fail_cannot_be_removed_from_the_allowlist(self):
        with self.assertRaises(ValueError):
            PhoneSettings(enabled=True, allowed=("launch",))

    def test_unicode_input_backend_parses_and_is_validated(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "unicode_input": "adbkeyboard"}), encoding="utf-8")
            self.assertEqual(load_settings(root).unicode_input, "adbkeyboard")
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "unicode_input": "ime"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_allowlist_parses_and_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text(json.dumps({
                "enabled": True, "allowed": ["launch", "tap_text", "done", "fail"],
                "allowed_subjects": ["u-1"]}), encoding="utf-8")
            settings = load_settings(root)
            self.assertEqual(settings.allowed, ("launch", "tap_text", "done", "fail"))
            self.assertEqual(settings.allowed_subjects, ("u-1",))
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "allowed": ["launch"]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)


class PhoneRecordTests(unittest.TestCase):
    def record(self) -> PhoneTaskRecord:
        return PhoneTaskRecord("phone-1", "{}", "打开应用签到", "active", 0, 4, (),
                               "2026-11-21T00:00:00+00:00", "2026-11-21T00:00:00+00:00",
                               "2026-11-21T02:00:00+00:00")

    def test_round_trip_and_optional_fields(self):
        parsed = PhoneTaskRecord.parse(self.record().document())
        self.assertEqual(parsed, self.record())
        with_inflight = PhoneTaskRecord.parse(
            replace(self.record(), inflight_at="2026-11-21T00:01:00+00:00").document())
        self.assertEqual(with_inflight.inflight_at, "2026-11-21T00:01:00+00:00")

    def test_unknown_shape_is_refused(self):
        with self.assertRaises(ValueError):
            PhoneTaskRecord.parse({"version": 1})
        with self.assertRaises(ValueError):
            PhoneTaskRecord.parse({**self.record().document(), "extra": True})


class PhoneServiceStateTests(unittest.TestCase):
    def test_records_are_saved_listed_and_cancelled(self):
        root = tempfile.mkdtemp(prefix="phone-action-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = Path(root) / "data"
        data.mkdir()
        runtime = StorageRuntime.open(data_root=data, installation_id="phone-test")
        try:
            service = PhoneActionService(runtime, settings_reader=lambda _root: PhoneSettings(enabled=True))
            record = PhoneTaskRecord("phone-1", "{}", "签到", "active", 0, 2, (),
                                     "2026-11-21T00:00:00+00:00", "2026-11-21T00:00:00+00:00",
                                     "2026-11-21T02:00:00+00:00")
            service._save(record)
            self.assertEqual(service._read("phone-1"), record)
            self.assertEqual([item.task_id for item in service._records()], ["phone-1"])
            self.assertTrue(service.cancel("phone-1"))
            self.assertEqual(service._read("phone-1").state, "cancelled")
            self.assertFalse(service.cancel("phone-1"))
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
