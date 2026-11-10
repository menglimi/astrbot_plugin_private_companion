"""Single-shot device capture keeps its original request and authority."""

import asyncio
from datetime import timedelta
import unittest

from companion_v2.capabilities import CapabilityAuthorizationState
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef
from companion_v2.screen import (
    SCREEN_EXTENSION, SCREEN_READ, ClockQuality, DeviceSnapshot,
    ScreenCaptureService, ScreenRepository,
)
from companion_v2.storage.repositories import OperationRepository

from tests.g6_support import G6Fixture, NOW
from tests.test_screen_originals import IMAGE


class Device:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    async def capture(self, device_id, *, before_dispatch):
        before_dispatch()
        self.calls += 1
        if self.callback:
            self.callback()
        return DeviceSnapshot(device_id, "desktop", NOW - timedelta(seconds=2),
                              ClockQuality.CLIENT_REPORTED, IMAGE, "Editor", ("secondary display unseen",))


class ScreenCaptureTests(G6Fixture):
    def setUp(self):
        super().setUp()
        self.enable_screen()
        self.screens = ScreenRepository(self.runtime, authorizer=self.identities)
        self.device = Device()

    def capture(self, *, request_id="request-a", device_id="desktop-a"):
        service = ScreenCaptureService(screens=self.screens, operations=OperationRepository(self.runtime),
                                       device=self.device, now=self.clock.now)
        return asyncio.run(service.capture_once(request_id=request_id, scope=self.scope,
            device_id=device_id, role=self.role, resolved=self.resolved,
            audience=AudienceRef(self.scope, (self.subject,))))

    def test_success_replay_and_restart_do_not_touch_device_twice(self):
        first = self.capture().unwrap()
        self.assertFalse(first.replayed)
        self.assertEqual(self.screens.read_image(first.capture.reference, resolved=self.resolved,
                                                 observed_at=NOW).unwrap(), IMAGE)
        self.assertTrue(self.capture().unwrap().replayed)
        self.assertEqual(self.device.calls, 1)
        snapshot = self.identities.snapshot()
        self.runtime.close()
        self.runtime = self.open_runtime()
        self.identities = type(self.identities).from_snapshot(snapshot)
        self.screens = ScreenRepository(self.runtime, authorizer=self.identities)
        self.assertTrue(self.capture().unwrap().replayed)
        self.assertEqual(self.device.calls, 1)

    def test_same_request_cannot_switch_device(self):
        self.capture().unwrap()
        self.assertEqual(self.capture(device_id="desktop-b").failure.code, "idempotency_key_conflict")
        self.assertEqual(self.device.calls, 1)

    def test_unknown_device_result_is_not_recaptured(self):
        def crash():
            raise RuntimeError("device reply lost")
        self.device.callback = crash
        self.assertEqual(self.capture().failure.code, "screen_capture_unknown")
        self.device.callback = None
        self.assertEqual(self.capture().failure.code, "screen_capture_unresolved")
        self.assertEqual(self.device.calls, 1)

    def test_mid_capture_revocation_prevents_publication(self):
        def revoke():
            self.capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
                capability_id=SCREEN_READ, subject=self.subject, scope=self.scope,
                state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
                observed_at=NOW).unwrap()
        self.device.callback = revoke
        self.assertEqual(self.capture().failure.code, "capability_consent_revoked")
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM sources", ())["n"], 0)
        self.assertEqual(self.device.calls, 1)

    def test_initial_denial_does_not_reserve_request(self):
        self.capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
            capability_id=SCREEN_READ, subject=self.subject, scope=self.scope,
            state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
            observed_at=NOW).unwrap()
        self.assertEqual(self.capture().failure.code, "capability_consent_revoked")
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM operations", ())["n"], 0)
        self.assertEqual(self.device.calls, 0)
        self.capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
            capability_id=SCREEN_READ, subject=self.subject, scope=self.scope,
            state=CapabilityAuthorizationState.ACTIVE, expected_revision=2,
            observed_at=NOW).unwrap()
        self.assertFalse(self.capture().unwrap().replayed)
        self.assertEqual(self.device.calls, 1)

    def test_revocation_before_device_io_reports_denial(self):
        fixture = self

        class RevokedBeforeDispatch(Device):
            async def capture(self, device_id, *, before_dispatch):
                fixture.capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
                    capability_id=SCREEN_READ, subject=fixture.subject, scope=fixture.scope,
                    state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
                    observed_at=NOW).unwrap()
                before_dispatch()
                self.calls += 1

        self.device = RevokedBeforeDispatch()
        self.assertEqual(self.capture().failure.code, "capability_consent_revoked")
        self.assertEqual(self.device.calls, 0)
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM screen_captures", ())["n"], 0)

    def test_cancellation_after_device_io_does_not_repeat_capture(self):
        def cancel():
            raise asyncio.CancelledError()

        self.device.callback = cancel
        with self.assertRaises(asyncio.CancelledError):
            self.capture()
        self.device.callback = None
        self.assertEqual(self.capture().failure.code, "screen_capture_unresolved")
        self.assertEqual(self.device.calls, 1)
        self.assertEqual(self.runtime.read_one("SELECT state FROM operations", ())["state"], "unknown")

    def test_device_must_call_dispatch_recheck(self):
        class SkippedRecheck(Device):
            async def capture(self, device_id, *, before_dispatch):
                self.calls += 1
                return DeviceSnapshot(device_id, "desktop", NOW,
                                      ClockQuality.CLIENT_REPORTED, IMAGE)

        self.device = SkippedRecheck()
        self.assertEqual(self.capture().failure.code, "screen_capture_unknown")
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM screen_captures", ())["n"], 0)

    def test_wrong_device_response_cannot_be_registered(self):
        class WrongDevice(Device):
            async def capture(self, device_id, *, before_dispatch):
                before_dispatch()
                self.calls += 1
                return DeviceSnapshot("other-device", "desktop", NOW,
                                      ClockQuality.CLIENT_REPORTED, IMAGE)
        self.device = WrongDevice()
        self.assertEqual(self.capture().failure.code, "screen_capture_unknown")
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM screen_captures", ())["n"], 0)

    def test_core_admin_needs_no_scope_consent_or_grant(self):
        scope = ScopeRef(self.scope.installation_id, "onebot", ScopeKind.PRIVATE, "private-core")
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO subject_access_tiers (subject_kind, subject_id, tier, revision, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(subject_kind, subject_id) DO UPDATE SET tier = excluded.tier""",
                ("human", self.subject.subject_id, "core", 1, NOW.isoformat()),
            )
        service = ScreenCaptureService(screens=self.screens, operations=OperationRepository(self.runtime),
                                       device=self.device, now=self.clock.now)
        captured = asyncio.run(service.capture_once(request_id="request-core", scope=scope,
            device_id="desktop-a", role=self.role, resolved=self.resolved,
            audience=AudienceRef(scope, (self.subject,))))
        self.assertTrue(captured.is_success, captured.failure)
        self.assertEqual(self.device.calls, 1)
        self.assertEqual(self.screens.read_image(captured.unwrap().capture.reference,
            resolved=self.resolved, observed_at=NOW).unwrap(), IMAGE)

    def make_core(self):
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO subject_access_tiers (subject_kind, subject_id, tier, revision, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(subject_kind, subject_id) DO UPDATE SET tier = excluded.tier""",
                ("human", self.subject.subject_id, "core", 1, NOW.isoformat()),
            )

    def revoke_screen_read(self):
        self.capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
            capability_id=SCREEN_READ, subject=self.subject, scope=self.scope,
            state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
            observed_at=NOW).unwrap()

    def test_core_explicit_revocation_blocks_capture_before_device_io(self):
        self.make_core()
        self.revoke_screen_read()
        self.assertEqual(self.capture().failure.code, "capability_consent_revoked")
        self.assertEqual(self.device.calls, 0)

    def test_core_revocation_during_capture_blocks_publication(self):
        self.make_core()
        self.device.callback = self.revoke_screen_read
        self.assertEqual(self.capture().failure.code, "capability_consent_revoked")
        self.assertEqual(self.device.calls, 1)
        self.assertEqual(self.runtime.read_one("SELECT count(*) AS n FROM screen_captures", ())["n"], 0)


if __name__ == "__main__":
    unittest.main()
