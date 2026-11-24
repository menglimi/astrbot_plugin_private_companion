"""Regression tests for phone role-layer configuration, ADB diagnostics and screen reading."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.role_migration import migrate_role_configuration_aliases
from companion_v2.configuration.service import ConfigurationService
from companion_v2.device.action_service import PhoneActionService, PhoneSettings, phone_profile
from companion_v2.device.adb import AdbClient, AdbError, UiElement, diagnose_adb_not_found, discover_adb
from companion_v2.device.annotate import annotate_screen, image_mime_type
from companion_v2.device.configuration import (
    GROUP as PHONE_GROUP,
    PhoneConfigurationOwner,
    PhoneSettingsReader,
    configuration_fields as phone_fields,
)
from companion_v2.device.session import PhoneObservation, PhoneSession
from companion_v2.execution.models import TaskContext
from companion_v2.foundation.refs import AudienceRef, OperationRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.storage.runtime import StorageRuntime
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_configuration_api import call as api_call
from tests.test_device_adb import FakeAdb, HIERARCHY, PNG
from tests.test_device_action_service import HostFixture, NOW, SCOPE, image_bytes
from tests.test_role_tool_loop import answered


class PhoneSettingsRealSnapshotAndMigrationTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(prefix="phone-role-migration-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.data = Path(root) / "data"
        self.data.mkdir()
        self.runtime = StorageRuntime.open(data_root=self.data, installation_id="install")
        self.addCleanup(self.runtime.close)
        self.fields = phone_fields()
        self.owner = PhoneConfigurationOwner(self.runtime)
        self.registry = FieldRegistry(version="test-phone-real", fields=self.fields)
        self.service = ConfigurationService(self.runtime, self.registry, owners=(self.owner,))

        # Register one core operator identity for real ConfigurationService.read()/patch().
        from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
        from companion_v2.identity_policy.repository import PersistentIdentityDirectory

        self.identity = PersistentIdentityDirectory(self.runtime)
        principal = AuthenticatedHostPrincipal("install", "dashboard", "dashboard", "admin-1")
        self.subject = SubjectRef("install", SubjectKind.HUMAN, "human-admin")
        self.identity.bind(
            binding_id="binding-admin-1",
            principal=principal,
            subject=self.subject,
            valid_from=NOW,
            expected_revision=self.identity.revision,
        )
        self._grant_seq = 0
        self.actor = self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()

    def _grant_target(self, target: ConfigTarget) -> None:
        install_target = ConfigTarget("install", ConfigLayer.INSTALLATION)
        for scope in (install_target.authorization_scope, target.authorization_scope):
            for capability in ("configuration.read", "configuration.write"):
                self._grant_seq += 1
                self.identity.issue_grant(
                    grant_id=f"grant-phone-{self._grant_seq}",
                    subject=self.subject,
                    scope=scope,
                    capability=capability,
                    tier=AuthorizationTier.CORE,
                    valid_from=NOW,
                    expected_revision=self.identity.revision,
                )

    def test_phone_settings_reader_honors_real_configuration_snapshot_fields_over_phone_json(self):
        role = SubjectRef("install", SubjectKind.BOT, "role-2517047265-6bc43383f16f9377")
        target = ConfigTarget("install", ConfigLayer.ROLE, role, bot_id="bot-2517047265")
        self._grant_target(target)

        # 1. No SQLite config and no phone.json -> default source.
        reader = PhoneSettingsReader(self.service, fallback_root=self.data)
        default_settings, source = reader.read_with_source(self.actor, target, NOW)
        self.assertEqual(source, "default")
        self.assertEqual(default_settings.adb, "")

        # 2. phone.json present, SQLite untouched -> phone.json wins.
        (self.data / "phone.json").write_text(
            json.dumps({"enabled": True, "adb": "/legacy/phone-json/adb"}), encoding="utf-8",
        )
        json_settings, source = reader.read_with_source(self.actor, target, NOW)
        self.assertEqual(source, "phone.json")
        self.assertEqual(json_settings.adb, "/legacy/phone-json/adb")

        # 3. Write real role-layer settings via ConfigurationService.patch() -> role_config MUST win!
        snap = self.service.read(self.actor, target=target, group_id=PHONE_GROUP, observed_at=NOW).unwrap()
        op = OperationRef("op-phone-1", self.owner.owner_id, target.authorization_scope, VersionRef("phone", 1), "k-1")
        self.service.patch(
            self.actor,
            target=target,
            group_id=PHONE_GROUP,
            operation=op,
            expected_edit_token=snap.edit_token,
            changes={
                PHONE_GROUP + ".enabled": True,
                PHONE_GROUP + ".adb_path": "D:/platform-tools/adb.exe",
                PHONE_GROUP + ".connect": "192.168.1.99:5555",
            },
            observed_at=NOW,
        ).unwrap()

        saved_settings, source = reader.read_with_source(self.actor, target, NOW)
        self.assertEqual(source, "role_config")
        self.assertTrue(saved_settings.enabled)
        self.assertEqual(saved_settings.adb, "D:/platform-tools/adb.exe")
        self.assertEqual(saved_settings.connect, "192.168.1.99:5555")

    def test_migrate_role_configuration_aliases_merges_split_role_layers_idempotently(self):
        legacy_role = SubjectRef("install", SubjectKind.BOT, "时音")
        canonical_role = SubjectRef("install", SubjectKind.BOT, "role-2517047265-6bc43383f16f9377")
        legacy_target = ConfigTarget("install", ConfigLayer.ROLE, legacy_role, bot_id="bot-2517047265")
        canonical_target = ConfigTarget("install", ConfigLayer.ROLE, canonical_role, bot_id="bot-2517047265")
        self._grant_target(canonical_target)

        # Settings were written under display-name role "时音".
        self.owner.write(
            legacy_target,
            self.fields,
            changes={
                PHONE_GROUP + ".enabled": True,
                PHONE_GROUP + ".adb_path": "C:/Android/platform-tools/adb.exe",
                PHONE_GROUP + ".connect": "192.168.1.88:37123",
                PHONE_GROUP + ".serial": "OLD-SERIAL",
            },
            reset=(),
            expected_revision=0,
            observed_at=NOW,
        )
        # Canonical layer already has an explicit newer serial that must NOT be overwritten.
        self.owner.write(
            canonical_target,
            self.fields,
            changes={PHONE_GROUP + ".serial": "NEW-SERIAL"},
            reset=(),
            expected_revision=0,
            observed_at=NOW,
        )

        first = migrate_role_configuration_aliases(
            self.runtime,
            persona_bindings=[("bot-2517047265", "时音", "role-2517047265-6bc43383f16f9377")],
            observed_at=NOW,
        )
        self.assertEqual(first["targets_migrated"], 1)
        self.assertEqual(first["fields_copied"], 3)

        reader = PhoneSettingsReader(self.service, fallback_root=self.data)
        merged, source = reader.read_with_source(self.actor, canonical_target, NOW)
        self.assertEqual(source, "role_config")
        self.assertTrue(merged.enabled)
        self.assertEqual(merged.adb, "C:/Android/platform-tools/adb.exe")
        self.assertEqual(merged.connect, "192.168.1.88:37123")
        self.assertEqual(merged.serial, "NEW-SERIAL")

        # Legacy rows are retained (non-destructive).
        legacy_count = self.runtime.read_one(
            "SELECT COUNT(*) AS n FROM configuration_values WHERE target_key = ?",
            (legacy_target.key,),
        )["n"]
        self.assertEqual(legacy_count, 4)

        # Second call is a strict no-op (idempotent).
        rev_before = self.runtime.read_one(
            "SELECT revision FROM configuration_targets WHERE target_key = ?",
            (canonical_target.key,),
        )["revision"]
        second = migrate_role_configuration_aliases(
            self.runtime,
            persona_bindings=[("bot-2517047265", "时音", "role-2517047265-6bc43383f16f9377")],
            observed_at=NOW,
        )
        self.assertEqual(second, {"targets_migrated": 0, "fields_copied": 0})
        rev_after = self.runtime.read_one(
            "SELECT revision FROM configuration_targets WHERE target_key = ?",
            (canonical_target.key,),
        )["revision"]
        self.assertEqual(rev_before, rev_after)


class PhoneConnectionDiagnosticsApiTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_adb_not_found_returns_structured_diagnostics_for_missing_dir_and_empty(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        first = [route for route in handlers
                 if route.split("/")[-1] == "config" and "/model/" not in route and "/targets/first/" in route][0]
        connect = handlers[first + "/phone-connection"]
        role_id = handlers[first].__self__.controller.role_id

        try:
            # 1. Auto-discovery empty (no explicit adb_path, no env/PATH adb).
            with patch.dict(os.environ, {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "LOCALAPPDATA": ""}, clear=False), \
                 patch("companion_v2.device.adb.shutil.which", return_value=None):
                scan_empty = await api_call(connect, {"action": "scan", "target_id": "first", "role_id": role_id})
                self.assertEqual(scan_empty["status"], "error", scan_empty)
                self.assertEqual(scan_empty["message"], "adb_not_found")
                self.assertEqual(scan_empty["data"]["reason"], "auto_discovery_empty")
                self.assertEqual(scan_empty["data"]["config_source"], "default")
                self.assertEqual(scan_empty["data"]["configured_adb_path"], "")
                self.assertEqual(scan_empty["data"]["resolved_role_id"], role_id)
                self.assertTrue(scan_empty["data"]["resolved_bot_id"])

                # Status endpoint also carries read-only adb_diagnostic for bridge fallback.
                status_resp = await api_call(handlers[first], query={"sections": "device.phone"})
                diag = status_resp["data"]["sections"]["device.phone"]["adb_diagnostic"]
                self.assertFalse(diag["available"])
                self.assertEqual(diag["reason"], "auto_discovery_empty")
                self.assertEqual(diag["resolved_role_id"], role_id)

            # 2. Explicit adb_path set to a directory -> explicit_path_is_dir.
            with tempfile.TemporaryDirectory() as folder:
                token = (await api_call(handlers[first], query={"sections": "device.phone"}))[
                    "data"]["sections"]["device.phone"]["edit_token"]
                saved = await api_call(handlers[first + "/configure"], {
                    "section": "device.phone",
                    "operation_key": "set-dir-adb",
                    "edit_token": token,
                    "changes": {"adb_path": folder},
                })
                self.assertEqual(saved["status"], "ok", saved)
                scan_dir = await api_call(connect, {"action": "scan"})
                self.assertEqual(scan_dir["status"], "error", scan_dir)
                self.assertEqual(scan_dir["message"], "adb_not_found")
                self.assertEqual(scan_dir["data"]["reason"], "explicit_path_is_dir")
                self.assertEqual(scan_dir["data"]["config_source"], "role_config")
                self.assertEqual(scan_dir["data"]["configured_adb_path"], folder)

            # 3. Explicit adb_path set to missing file -> explicit_path_missing.
            missing_file = "C:/definitely-not-here/platform-tools/adb.exe"
            token = (await api_call(handlers[first], query={"sections": "device.phone"}))[
                "data"]["sections"]["device.phone"]["edit_token"]
            saved = await api_call(handlers[first + "/configure"], {
                "section": "device.phone",
                "operation_key": "set-missing-adb",
                "edit_token": token,
                "changes": {"adb_path": missing_file},
            })
            self.assertEqual(saved["status"], "ok", saved)
            scan_missing = await api_call(connect, {"action": "scan"})
            self.assertEqual(scan_missing["status"], "error", scan_missing)
            self.assertEqual(scan_missing["message"], "adb_not_found")
            self.assertEqual(scan_missing["data"]["reason"], "explicit_path_missing")
            self.assertEqual(scan_missing["data"]["config_source"], "role_config")
            self.assertEqual(scan_missing["data"]["configured_adb_path"], missing_file)

            # 4. Mismatched target_id on scoped route is rejected.
            wrong = await api_call(connect, {"action": "scan", "target_id": "second", "role_id": role_id})
            self.assertEqual(wrong["status"], "error")
            self.assertEqual(wrong["message"], "configuration_target_changed")
        finally:
            self.controller.unregister_routes()


class PhoneScreenReadAndObserveTests(unittest.TestCase):
    def test_ui_dump_falls_back_to_data_local_tmp_instead_of_sdcard(self):
        fake = FakeAdb()
        fake.failures[("-s", "SER123", "exec-out", "uiautomator", "dump", "--compressed", "/dev/tty")] = "Permission denied"
        fake.failures[("-s", "SER123", "exec-out", "uiautomator", "dump", "/dev/tty")] = "Permission denied"
        fake.responses[("-s", "SER123", "shell", "uiautomator", "dump", "/data/local/tmp/companion-window.xml")] = (
            "UI hierchary dumped to: /data/local/tmp/companion-window.xml\n"
        )
        fake.responses[("-s", "SER123", "exec-out", "cat", "/data/local/tmp/companion-window.xml")] = HIERARCHY

        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        elements = client.ui_dump()
        self.assertEqual(len(elements), 4)
        called_args = [" ".join(cmd) for cmd in fake.calls]
        self.assertTrue(any("/data/local/tmp/companion-window.xml" in cmd for cmd in called_args))
        self.assertFalse(any("/sdcard/companion-window.xml" in cmd for cmd in called_args))

    def test_observe_degrades_to_vision_only_when_ui_dump_fails(self):
        fake = FakeAdb()
        fake.failures[("-s", "SER123", "exec-out", "uiautomator", "dump", "--compressed", "/dev/tty")] = (
            "ERROR: could not get idle state."
        )
        fake.failures[("-s", "SER123", "exec-out", "uiautomator", "dump", "/dev/tty")] = (
            "ERROR: could not get idle state."
        )
        fake.failures[("-s", "SER123", "shell", "uiautomator", "dump", "/data/local/tmp/companion-window.xml")] = (
            "ERROR: could not get idle state."
        )

        client = AdbClient("adb.exe", serial="SER123", runner=fake)
        session = PhoneSession(client, goal="看看现在手机屏幕上显示什么")
        observation = session.observe(0)

        self.assertEqual(observation.elements, ())
        self.assertTrue(observation.ui_dump_degraded)
        self.assertEqual(observation.ui_dump_error, "adb_ui_failed")
        self.assertEqual(observation.screenshot, PNG)
        prompt = observation.prompt_text(screenshot_attached=True)
        self.assertIn("本页未能读取控件树", prompt)
        self.assertIn("已附上截图", prompt)

        # When screenshot attachment failed, prompt MUST NOT claim screenshot is attached.
        prompt_no_img = observation.prompt_text(screenshot_attached=False)
        self.assertNotIn("已附上截图", prompt_no_img)
        self.assertIn("未能成功编码附带", prompt_no_img)

    def test_screenshot_detects_flag_secure_and_accepts_10mb_2k_png(self):
        # Empty stdout with returncode=0 -> FLAG_SECURE.
        def secure_empty(args, timeout):
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with self.assertRaises(AdbError) as caught:
            AdbClient("adb.exe", serial="SER123", runner=secure_empty).screenshot()
        self.assertEqual(caught.exception.code, "adb_screenshot_secure")

        # Stderr mentioning FLAG_SECURE -> adb_screenshot_secure.
        def secure_stderr(args, timeout):
            return subprocess.CompletedProcess(args, 1, b"", b"Status: FLAG_SECURE window cannot be captured")

        with self.assertRaises(AdbError) as caught2:
            AdbClient("adb.exe", serial="SER123", runner=secure_stderr).screenshot()
        self.assertEqual(caught2.exception.code, "adb_screenshot_secure")

        # 10 MB PNG (> old 8 MB limit, < new 24 MB limit) is accepted.
        big_png = PNG + b"\x00" * (10 * 1024 * 1024)

        def large_ok(args, timeout):
            return subprocess.CompletedProcess(args, 0, big_png, b"")

        result = AdbClient("adb.exe", serial="SER123", runner=large_ok).screenshot()
        self.assertEqual(len(result), len(big_png))

    def test_annotate_screen_progressive_downscale_and_jpeg_fallback(self):
        # High-entropy noisy image that exceeds a tight byte budget as PNG but succeeds via JPEG fallback.
        rng = random.Random(42)
        width, height = 720, 1280
        raw = bytes(rng.getrandbits(8) for _ in range(width * height * 3))
        img = Image.frombytes("RGB", (width, height), raw)
        buf = BytesIO()
        img.save(buf, "PNG")
        noisy_png = buf.getvalue()

        # Budget tight enough that full & 0.7x PNG exceed it, forcing progressive downscale + JPEG fallback.
        budget = 180_000
        annotated = annotate_screen(noisy_png, (), max_bytes=budget, boxes=False)
        self.assertLessEqual(len(annotated), budget)
        self.assertEqual(image_mime_type(annotated), "image/jpeg")

    def test_discover_adb_and_diagnose_directory_vs_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(AdbError) as caught:
                discover_adb(folder)
            self.assertEqual(caught.exception.code, "adb_not_found")
            self.assertEqual(diagnose_adb_not_found(folder)["reason"], "explicit_path_is_dir")
        self.assertEqual(diagnose_adb_not_found("C:/no-such/adb.exe")["reason"], "explicit_path_missing")
        self.assertEqual(diagnose_adb_not_found("")["reason"], "auto_discovery_empty")


class PhoneNonShoppingNoticeTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (phone_profile("primary", max_output_tokens=100),)
        self.providers = tuple(
            NS(**{**p.__dict__, "capabilities": ("text", "image")})
            if not hasattr(p, "__dataclass_fields__")
            else __import__("dataclasses").replace(p, capabilities=("text", "image"))
            for p in self.providers
        )
        from companion_v2.model_gateway.routing import configuration_fields
        self.registry = FieldRegistry(version="test-phone-notice", fields=configuration_fields(self.profiles))

    async def test_non_shopping_read_screen_task_delivers_completion_notice(self):
        host = await self.start()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="test-device")
        service._client_factory = lambda _settings: Mock()
        screen = image_bytes("green", size=(200, 400))
        observation = PhoneObservation(0, 200, 400, (), "screen-hash", len(screen), screen,
                                       stable=False, ui_dump_degraded=True, ui_dump_error="adb_ui_failed")

        async def decide(_envelope):
            return answered(json.dumps({"action": "done", "reason": "当前页面显示的是音乐播放列表，正在播放《晴天》。"},
                                       ensure_ascii=False))

        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", return_value=observation):
            task_id = service.create(context, "看看我手机现在什么界面", shopping=False)["task_id"]
            await self.until(lambda: service.status(context, task_id)["state"] == "done")
            await self.until(lambda: bool(self.sent) and self.runtime.read_one(
                "SELECT 1 FROM background_deliveries WHERE owner = 'phone.notice' AND state = 'delivered'", (),
            ))
            self.assertIn("音乐播放列表", self.sent[0].text)


class MobileGatewayTelemetryAndPersonaTests(unittest.TestCase):
    def _make_gateway(self, cfg, *, health_enabled=False, health_ttl_seconds=3600):
        from astrbot_host.native_extensions.reality.mobile_gateway import MobileGatewayMixin

        class _FakeGateway(MobileGatewayMixin):
            def __init__(self, data):
                self._data = data
                self.health_enabled = health_enabled
                self.health_ttl_seconds = health_ttl_seconds

            def _cfg_bool(self, key, default=False):
                return bool(self._data.get(key, default))

            def _cfg_int(self, key, default=0, low=0, high=10**9):
                val = int(self._data.get(key, default))
                return max(low, min(high, val))

        return _FakeGateway(cfg)

    def test_mobile_telemetry_enabled_honors_mobile_flag_even_when_health_enabled_false(self):
        gw = self._make_gateway(
            {"mobile.telemetry_enabled": True, "mobile.telemetry_ttl_seconds": 1800},
            health_enabled=False,
            health_ttl_seconds=3600,
        )
        self.assertTrue(gw._mobile_telemetry_enabled())
        self.assertEqual(gw._mobile_telemetry_ttl(), 1800)

        gw_health_only = self._make_gateway(
            {"mobile.telemetry_enabled": False, "mobile.telemetry_ttl_seconds": 1800},
            health_enabled=True,
            health_ttl_seconds=7200,
        )
        self.assertTrue(gw_health_only._mobile_telemetry_enabled())
        self.assertEqual(gw_health_only._mobile_telemetry_ttl(), 7200)

        gw_off = self._make_gateway(
            {"mobile.telemetry_enabled": False},
            health_enabled=False,
        )
        self.assertFalse(gw_off._mobile_telemetry_enabled())

    def test_mobile_persona_display_name_strips_long_parenthetical_aliases_cleanly(self):
        from astrbot_host.native_extensions.reality.mobile_gateway import _mobile_persona_display_name

        self.assertEqual(_mobile_persona_display_name("星野凛（Rin Hoshino / 凛）", 12), "星野凛")
        self.assertEqual(_mobile_persona_display_name("白河汐 / Shirakawa Shio", 12), "白河汐")
        self.assertEqual(_mobile_persona_display_name("小星", 12), "小星")
        self.assertEqual(_mobile_persona_display_name("一二三四五六七八九十甲（超长别名）", 12), "一二三四五六七八九十甲")
        self.assertFalse(_mobile_persona_display_name("A Very Long Name (Alias)", 12).endswith(("(", "（", "/", " ")))


if __name__ == "__main__":
    unittest.main()
