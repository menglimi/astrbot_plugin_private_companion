"""Remote Screen device selection, authentication, receipts and native host adoption."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from astrbot_host.native_extensions.screen_source import DEFAULTS, ScreenSourceSettings, capture_remote, validate
from tests import test_native_screen_flow as native
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityRepository
from companion_v2.screen import SCREEN_EXTENSION, SCREEN_READ, SCREEN_PROCESS
from tests.test_management_access import NOW, SCOPE
from tools.remote_screen_collector import capture_desktop, make_server


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "green").save(output, format="JPEG")
    return output.getvalue()


class CollectorBackendTests(unittest.TestCase):
    def test_niri_uses_screencopy_without_clipboard_or_portal(self):
        from types import SimpleNamespace
        with patch("tools.remote_screen_collector.sys.platform", "linux"), patch.dict(
                "os.environ", {"WAYLAND_DISPLAY": "wayland-1", "NIRI_SOCKET": "/run/user/1000/niri.sock"}), patch(
                "tools.remote_screen_collector.subprocess.run", return_value=SimpleNamespace(stdout=jpeg())) as run, patch(
                "tools.remote_screen_collector.portal_image") as portal:
            content = capture_desktop({"backend": "auto"})
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.format, "JPEG")
        run.assert_called_once_with(["grim", "-"], check=True, capture_output=True, timeout=12)
        portal.assert_not_called()


class RemoteTransportTests(unittest.TestCase):
    def setUp(self):
        self.count = 0
        self.now = datetime.now(timezone.utc)
        self.config = {"token": secrets.token_urlsafe(48), "device_id": "test-linux",
                       "listen": "127.0.0.1", "port": 0}
        self.server = make_server(self.config, capture=self.capture, now=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.settings = {**DEFAULTS, "mode": "remote", "device_id": self.config["device_id"],
            "remote_url": "http://127.0.0.1:%d/capture" % self.server.server_address[1],
            "remote_token": self.config["token"]}

    def capture(self, _):
        self.count += 1
        return jpeg()

    def test_authenticated_original_has_correct_device_time_and_bytes(self):
        result = capture_remote(self.settings, "request-a", now=self.now)
        self.assertEqual((result.device_id, result.content), ("test-linux", jpeg()))
        self.assertEqual(datetime.fromisoformat(result.captured_at), self.now)
        self.assertEqual(self.count, 1)

    def test_repeated_request_replays_same_capture(self):
        first = capture_remote(self.settings, "request-a", now=self.now)
        second = capture_remote(self.settings, "request-a", now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(self.count, 1)

    def test_wrong_credential_is_rejected_before_capture(self):
        with self.assertRaises(HTTPError) as failure:
            capture_remote({**self.settings, "remote_token": secrets.token_urlsafe(48)}, "request-a", now=self.now)
        self.assertEqual(failure.exception.code, 403)
        self.assertEqual(self.count, 0)

    def test_wrong_device_is_rejected_before_capture(self):
        with self.assertRaises(HTTPError):
            capture_remote({**self.settings, "device_id": "other-desktop"}, "request-a", now=self.now)
        self.assertEqual(self.count, 0)

    def test_browser_origin_is_rejected_even_with_credential(self):
        command = {"schema": "companion.screen-capture.v1", "device_id": "test-linux", "request_id": "request-a"}
        outgoing = Request(self.settings["remote_url"], data=json.dumps(command).encode(), headers={
            "Authorization": "Bearer " + self.config["token"], "Origin": "https://example.invalid",
            "Content-Type": "application/json"})
        with self.assertRaises(HTTPError):
            urlopen(outgoing)
        self.assertEqual(self.count, 0)

    def test_old_capture_and_invalid_image_are_not_adopted(self):
        with self.assertRaises(ValueError):
            capture_remote(self.settings, "old", now=self.now + timedelta(minutes=2))
        self.server.collector.capture = lambda _: b"not an image"
        with self.assertRaises(Exception):
            capture_remote(self.settings, "bad-image", now=self.now)

    def test_unknown_device_result_is_not_retried(self):
        self.server.collector.capture = lambda _: (_ for _ in ()).throw(OSError("desktop unavailable"))
        for _ in range(2):
            with self.assertRaises(HTTPError):
                capture_remote(self.settings, "failed", now=self.now)
        self.assertEqual(len(self.server.collector.results), 1)

    def test_settings_preserve_secrets_and_require_current_revision(self):
        with TemporaryDirectory() as temporary:
            source = ScreenSourceSettings(temporary)
            first = source.snapshot()
            self.assertEqual(first["settings"]["mode"], "local")
            saved = source.update(self.settings, first["revision"])
            self.assertTrue(saved["token_configured"])
            self.assertNotIn(self.config["token"], json.dumps(saved))
            source.update({"remote_token": "", "device_id": "test-windows"}, saved["revision"])
            self.assertEqual(source.read()["remote_token"], self.config["token"])
            with self.assertRaises(ValueError):
                source.update({"mode": "local"}, first["revision"])

    def test_insecure_public_endpoints_and_unknown_configuration_are_rejected(self):
        for updates in ({"remote_url": "http://8.8.8.8/capture"},
                        {"remote_url": "http://127.0.0.1/capture?next=example"},
                        {"remote_url": "http://user:pass@127.0.0.1/capture"},
                        {"timeout_seconds": True}, {"unexpected": "value"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate({**self.settings, **updates})

    def test_redirect_does_not_forward_credentials(self):
        forwarded = []

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path == "/capture":
                    self.send_response(307)
                    self.send_header("Location", "/credential-sink")
                else:
                    forwarded.append(self.headers.get("Authorization"))
                    self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = {**self.settings, "remote_url": "http://127.0.0.1:%d/capture" % server.server_address[1]}
        with self.assertRaises(ValueError):
            capture_remote(settings, "redirect", now=self.now)
        self.assertEqual(forwarded, [])
        self.assertEqual(self.count, 0)


class RemoteScreenFlowTests(native.HostFixture, unittest.IsolatedAsyncioTestCase):
    """Exercise the production peek/adoption/interpretation pipeline over HTTP."""

    capture = native.NativeScreenFlowTests.capture
    consent = native.NativeScreenFlowTests.consent
    screen_model = native.NativeScreenFlowTests.screen_model
    screen_host = native.NativeScreenFlowTests.screen_host
    diagnostic_host = native.NativeScreenFlowTests.diagnostic_host

    def setUp(self):
        super().setUp()
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM capability_descriptors WHERE extension_id = ?", (SCREEN_EXTENSION,))
        self.jpeg, self.captures, self.capture_error = native.sample_jpeg(), 0, None
        self.provider_directory = self.root.parent / "screen-native-remote"
        self.native_registry = self.create_screen_registry()
        self.providers = tuple(replace(provider, capabilities=("text", "tools", "image"))
                               for provider in self.providers)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=SCREEN_EXTENSION, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.capabilities.set_role_enabled(extension_id=SCREEN_EXTENSION, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for capability in (SCREEN_READ, SCREEN_PROCESS):
            self.grant(self.important, SCOPE, capability)
            self.consent(capability, CapabilityAuthorizationState.ACTIVE, 0)
        self.model_behavior = self.screen_model

    def create_screen_registry(self):
        registry = native.NativeScreenFlowTests.create_screen_registry(self)
        config = {"token": secrets.token_urlsafe(48), "device_id": "remote-test-desktop",
                  "listen": "127.0.0.1", "port": 0}
        server = make_server(config, capture=lambda _: self.capture(), now=self.clock.now)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        source = self.screen_provider.source
        source.update({"mode": "remote", "remote_url": "http://127.0.0.1:%d/capture" % server.server_address[1],
                       "remote_token": config["token"], "device_id": config["device_id"]},
                      source.snapshot()["revision"])
        return registry

    test_remote_original_reaches_interpretation_review_and_delivery = (
        native.NativeScreenFlowTests.test_screen_diagnostic_captures_and_interprets_its_new_original_then_delivers)

    test_uncertain_remote_request_is_not_recaptured = (
        native.NativeScreenFlowTests.test_screen_diagnostic_unknown_device_is_visible_and_same_attempt_never_recaptures)
