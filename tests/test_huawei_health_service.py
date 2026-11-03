from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.huawei_health.api import HuaweiHealthWebAPI
from astrbot_host.huawei_health.service import HuaweiHealthService
from astrbot_host.huawei_health.vendor import hw_credentials


class HuaweiHealthApiContractTests(unittest.TestCase):
    def test_status_and_credential_routes_share_the_service_resolver(self):
        self.assertTrue(callable(getattr(HuaweiHealthWebAPI, "_service", None)))
        self.assertTrue(callable(getattr(HuaweiHealthWebAPI, "status", None)))


class HuaweiHealthAclTests(unittest.TestCase):
    def test_current_process_sid_is_used_before_inheritance_is_removed(self):
        sid = "S-1-5-21-123-456-789-1001"
        results = [SimpleNamespace(returncode=0, stdout='"HOST\\user","' + sid + '"\n'),
                   SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)]
        with patch.object(hw_credentials.os, "name", "nt"), \
                patch.object(hw_credentials.os.path, "isdir", return_value=True), \
                patch.object(hw_credentials.subprocess, "run", side_effect=results) as run:
            self.assertEqual(hw_credentials.harden_dir_permissions("health-dir"), "")
        self.assertEqual(run.call_args_list[0].args[0], ["whoami", "/user", "/fo", "csv", "/nh"])
        self.assertIn("*" + sid + ":(OI)(CI)F", run.call_args_list[1].args[0])
        self.assertEqual(run.call_args_list[2].args[0], ["icacls", "health-dir", "/inheritance:r"])

    def test_unverified_identity_never_removes_inherited_access(self):
        with patch.object(hw_credentials.os, "name", "nt"), \
                patch.object(hw_credentials.os.path, "isdir", return_value=True), \
                patch.object(hw_credentials.subprocess, "run",
                             return_value=SimpleNamespace(returncode=1, stdout="")) as run:
            self.assertIn("SID", hw_credentials.harden_dir_permissions("health-dir"))
        self.assertEqual(run.call_count, 1)

    def test_failed_grant_does_not_remove_inherited_access(self):
        results = [SimpleNamespace(returncode=0, stdout='"HOST\\user","S-1-5-21-123-456-789-1001"\n'),
                   SimpleNamespace(returncode=5)]
        with patch.object(hw_credentials.os, "name", "nt"), \
                patch.object(hw_credentials.os.path, "isdir", return_value=True), \
                patch.object(hw_credentials.subprocess, "run", side_effect=results) as run:
            self.assertIn("授权返回码 5", hw_credentials.harden_dir_permissions("health-dir"))
        self.assertEqual(run.call_count, 2)


class HuaweiHealthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_and_persistent_config_are_bounded_and_offline_safe(self):
        with tempfile.TemporaryDirectory(prefix="companion-huawei-health-") as directory:
            service = HuaweiHealthService(directory, logger=logging.getLogger("test"))
            await service.initialize()
            status = await service.status()
            self.assertFalse(status["bound"])
            self.assertFalse(status["token"]["cached"])
            self.assertIn("browser", status)
            service.config["headless"] = False
            self.assertTrue(service.config.save_config())
            restored = HuaweiHealthService(directory, logger=logging.getLogger("test"))
            self.assertFalse(restored.config["headless"])

    async def test_completed_records_create_weekly_and_recovery_materials(self):
        with tempfile.TemporaryDirectory(prefix="companion-huawei-health-") as directory:
            service = HuaweiHealthService(directory, logger=logging.getLogger("test"))
            now = datetime.now(timezone.utc)
            end_ns = int(now.timestamp() * 1e9)
            end_ms = int(now.timestamp() * 1000)
            sleep = [{"startTime": end_ns - 8 * 3600 * 10**9, "endTime": end_ns,
                      "value": [{"fieldName": "all_sleep_time", "floatValue": 430}]}]
            activities = [{"startTime": end_ms - 32 * 60000, "endTime": end_ms,
                           "activityType": 1, "distance": 5200, "duration": 32 * 60000}]
            hr = {"daily": [{"day": f"2026-09-{day:02d}", "restBpm": {"avg": value}}
                            for day, value in zip(range(24, 31), (50, 51, 52, 60, 61, 62, 63))]}
            hrv = {"daily": [{"day": f"2026-09-{day:02d}", "hrv": {"avg": value}}
                             for day, value in zip(range(24, 31), (60, 58, 59, 40, 42, 41, 39))]}
            events = service._health_events(sleep, activities, hr, hrv)
            kinds = {item["kind"] for item in events}
            self.assertIn("sleep_completed", kinds)
            self.assertIn("workout_completed", kinds)
            self.assertIn("weekly_review", kinds)
            self.assertIn("recovery_trend", kinds)

    async def test_cached_summary_expires_instead_of_claiming_stale_health(self):
        with tempfile.TemporaryDirectory(prefix="companion-huawei-health-") as directory:
            service = HuaweiHealthService(directory, logger=logging.getLogger("test"))
            service._latest = {
                "basis": "huawei_health_cloud",
                "observed_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "summary": "旧摘要",
            }
            self.assertIsNone(service.cached_context(max_age_seconds=3600))
            service._latest["observed_at"] = datetime.now(timezone.utc).isoformat()
            self.assertEqual(service.cached_context(max_age_seconds=3600)["summary"], "旧摘要")
            self.assertTrue(service.allowed_for("any-user", "any-role"))
            service.bind_scope("user-1", "role-1")
            self.assertTrue(service.allowed_for("user-1", "role-1"))
            self.assertFalse(service.allowed_for("user-2", "role-1"))
            self.assertFalse(service.allowed_for("user-1", "role-2"))


if __name__ == "__main__":
    unittest.main()
