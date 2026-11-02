"""Integration metadata and scoped workspaces; never touches live plugin stores."""
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request
from astrbot_host.integration_registry import IntegrationRegistry, STATUS_SCHEMA
from astrbot_host.integration_catalog import OPTIONAL_PROVIDERS
from astrbot_host.native_extensions import NativeExtensionRegistry
from astrbot_host.native_extensions.builtins import create_bundled_extensions, MANAGED_EXTENSIONS
from astrbot_host.management_identity import dashboard_operator
from companion_v2.capabilities import CapabilityRepository
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.product import PRODUCT_VERSION

PREFIX = "/astrbot_plugin_private_companion"


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.spec = {"plugin_id": "synthetic.third_party", "capability_id": "third_party",
            "label": "Synthetic third-party provider", "description": "Metadata contract fixture",
            "furniture": {"id": "third-party", "name": "Third-party fixture", "panel_id": "third-party"}}
        providers = (self.spec, *(item for item in OPTIONAL_PROVIDERS if item.get("owner") == "companion_v2"))
        catalog_patch = patch("astrbot_host.integration_registry.OPTIONAL_PROVIDERS", providers)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        self.provider = Mock(return_value={"schema": STATUS_SCHEMA, "plugin_id": self.spec["plugin_id"],
            "enabled": True, "runtime_ready": None, "private_record": "must-not-escape", "token": "secret"})
        self.api = NS(companion_v2_status=self.provider, status=Mock(side_effect=AssertionError("private API")))
        self.star = NS(root_dir_name=self.spec["plugin_id"], version="1.0", activated=True,
                       star_cls=NS(extension_api=self.api), pages=[])
        self.native = NS(get_all_stars=lambda: [self.star])
        self.registry = IntegrationRegistry(self.native)

    def item(self):
        return next(item for item in self.registry.snapshot()["providers"] if item["plugin_id"] == self.spec["plugin_id"])

    def test_native_catalog_conflict_is_visible_without_claiming_execution_or_losing_status(self):
        snapshot = self.registry.snapshot(native_issues=((self.spec["plugin_id"], "capability_descriptor_conflict"),))
        item = next(p for p in snapshot["providers"] if p["plugin_id"] == self.spec["plugin_id"])
        self.assertTrue(item["status_connected"])
        self.assertEqual(item["integration_state"], "degraded")
        self.assertEqual(item["execution_issue"], "capability_descriptor_conflict")
        self.assertFalse(item["execution_integrated"])
        self.assertIn("当前不能在对话中调用", item["reason"])

    def test_only_metadata_contract_is_invoked_and_private_fields_are_dropped(self):
        value = self.item()
        self.assertTrue(value["status_connected"])
        self.assertIsNone(value["runtime_ready"])
        self.assertFalse(value["execution_integrated"])
        self.assertNotIn("secret", json.dumps(value))
        self.assertNotIn("must-not-escape", json.dumps(value))
        self.api.status.assert_not_called()

    def test_disabled_stale_instance_and_duplicate_identity_never_invoked(self):
        self.star.activated = False
        self.assertEqual(self.item()["integration_state"], "disabled")
        self.provider.assert_not_called()
        self.star.activated = True
        self.native.get_all_stars = lambda: [self.star, self.star]
        self.assertEqual(self.item()["integration_state"], "conflict")
        self.provider.assert_not_called()

    def test_hot_replacement_missing_invalid_and_async_status(self):
        self.star.star_cls = NS(extension_api=NS())
        self.assertEqual(self.item()["integration_state"], "installed")
        self.star.star_cls.extension_api = NS(companion_v2_status=lambda: {"schema":STATUS_SCHEMA,"enabled":"true"})
        self.assertEqual(self.item()["integration_state"], "degraded")
        self.star.star_cls.extension_api = self.api
        self.provider.return_value["runtime_ready"] = False
        self.assertEqual(self.item()["integration_state"], "waiting")
        async def async_status():
            raise AssertionError("async discovery must not run")
        self.star.star_cls.extension_api = NS(companion_v2_status=async_status)
        self.assertEqual(self.item()["integration_state"], "installed")
        self.native.get_all_stars = lambda: []
        self.assertEqual(self.item()["integration_state"], "missing")

    def test_discovery_error_does_not_claim_plugins_are_missing(self):
        self.native.get_all_stars = Mock(side_effect=RuntimeError("private path"))
        value = self.registry.snapshot()
        external = [item for item in value["providers"] if item["owner"] == "external"]
        bundled = value["extensions"]
        self.assertTrue(external)
        self.assertTrue(all(item["integration_state"] == "unknown" for item in external))
        self.assertTrue(bundled)
        self.assertTrue(all(item["integration_state"] == "degraded" for item in bundled))
        self.assertNotIn("private path", json.dumps(value))

    def test_unreadable_optional_page_directory_keeps_status_available(self):
        with patch.object(Path, "is_dir", return_value=True), patch.object(
            Path, "iterdir", side_effect=PermissionError("unreadable source page directory")
        ):
            value = self.item()
        self.assertTrue(value["status_connected"])
        self.assertEqual(value["pages"], [])

    async def test_seven_bundled_providers_report_owned_metadata_without_starting_services(self):
        with TemporaryDirectory(prefix="companion-integration-bundled-") as temporary:
            context = NS(get_all_stars=Mock(return_value=()))
            leases, issues = create_bundled_extensions(Path(temporary), context=context,
                controller=NS(), now=lambda: support.NOW)
            self.assertEqual(issues, ())
            registry = NativeExtensionRegistry(context, bundled_extensions=leases,
                managed_extensions=MANAGED_EXTENSIONS)
            self.addAsyncCleanup(registry.aclose)
            self.assertEqual(len(leases), 7)
            self.assertTrue(all(lease.star is None for lease in leases))
            declarations = registry.declarations()
            self.assertEqual({item.descriptor.package.extension_id for item in declarations},
                {"content", "image", "companion.screen.native", "reality", "together", "game", "live"})
            for item in declarations:
                package = item.descriptor.package
                base, separator, build = package.package_version.partition("+")
                self.assertEqual(base, PRODUCT_VERSION, package.extension_id)
                if separator:
                    # Image transports are rebuilt within one release; their package
                    # version carries a content hash, while the release base must match.
                    self.assertEqual(package.extension_id, "image", package.package_version)
                    self.assertRegex(build, r"^image\.[0-9a-f]{64}$")
            value = IntegrationRegistry(context).snapshot(native_extensions=registry)
            bundled = value["extensions"]
            self.assertEqual({item["capability_id"] for item in bundled},
                {"content", "image", "screen", "reality", "together", "game", "live"})
            for item in bundled:
                self.assertEqual(item["kind"], "native_extension")
                self.assertEqual(item["owner"], "companion_v2")
                base, separator, build = item["version"].partition("+")
                self.assertEqual(base, PRODUCT_VERSION, item["capability_id"])
                if separator:
                    self.assertEqual(item["capability_id"], "image", item["version"])
                    self.assertRegex(build, r"^image\.[0-9a-f]{64}$")
                self.assertTrue(item["status_connected"])
                self.assertFalse(item["assembled"])
                self.assertFalse(item["runtime_ready"])
                self.assertEqual(item["runtime"]["state"], "inactive")
                self.assertEqual(item["pages"], [])
            self.assertTrue(all(lease.state == "inactive" and lease._start_task is None
                                for lease in leases))
            await registry.aclose()


class MultiIntegrationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller.register_routes()
        self.routes = {path: handler for path, handler, *_ in self.native.registered_web_apis}

    async def request(self, path, *, username="admin", query="", method="GET", body=None):
        payload = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if body is not None else b"")

        async def receive():
            return {"type":"http.request", "body":payload, "more_body":False}

        headers = [(b"content-type", b"application/json")] if payload else []
        req = Request({"type":"http", "method":method, "path":path, "headers":headers,
                       "query_string":query.encode()}, receive=receive)
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.routes[path]()

    async def test_manifest_readable_before_initialization_and_cleanup_is_owned(self):
        path = PREFIX + "/integration/manifest"
        for name in (None,"","api_key:synthetic"):
            self.assertEqual((await self.request(path,username=name))["data"]["code"], "dashboard_identity_required")
        result = await self.request(path)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["product"]["version"], PRODUCT_VERSION)
        self.assertIsNone(self.controller.runtime)
        self.assertEqual((await self.request(path,query="target_id=other"))["data"]["code"], "integration_request_invalid")
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request(path))["data"]["code"], "staging_host_terminated")

    async def test_scoped_relationship_growth_and_life_read_current_target_and_role(self):
        (await self.controller.initialize_profile()).unwrap()
        inventory = (await self.request(PREFIX + "/page/targets"))["data"]["targets"]
        # The Dashboard directory resolves display labels with bounded, read-only
        # OneBot lookups. Scoped domain reads below must not issue more host I/O.
        lookups = self.native.platform.client._api._wsr_api.calls
        self.assertEqual({action for action, _ in lookups},
                         {"get_friend_list", "get_stranger_info", "get_login_info"})
        lookups.clear()
        contexts = set()
        for target in inventory:
            for role in target["roles"]:
                for name in ("growth", "relationships", "life"):
                    response = await self.request(role[name + "_api"])
                    self.assertEqual(response["status"], "ok", (name, target["target_id"], role["role_id"], response))
                    if name in ("growth", "relationships"):
                        self.assertEqual(response["data"]["profile_id"], target["target_id"])
                    if name == "growth":
                        contexts.add(response["data"]["context_key"])
        self.assertEqual(len(contexts),4)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(lookups, [])
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,expected_revision=policy.revision,observed_at=support.NOW)
        self.assertEqual((await self.request(PREFIX + "/integration/manifest"))["data"]["code"], "core_access_required")

    async def test_v2_image_and_screen_furniture_lifecycle_retains_data_and_reassembles(self):
        await self.start_host()
        path = PREFIX + "/integration/lifecycle"
        manifest_path = PREFIX + "/integration/manifest"

        async def command(extension_id, action, revision, key, *, username="admin"):
            return await self.request(path, method="POST", username=username, body={
                "extension_id":extension_id, "action":action, "expected_revision":revision,
                "operation_key":key,
            })

        before = (await self.request(manifest_path))["data"]
        by_capability = {item["capability_id"]:item for item in before["extensions"]}
        for capability, extension_id in (("image", "image"), ("screen", "companion.screen.native")):
            item = by_capability[capability]
            self.assertEqual(item["owner"], "companion_v2")
            self.assertEqual(item["extension_id"], extension_id)
            self.assertFalse(item["assembled"])
            self.assertFalse(item["runtime_ready"])
            self.assertEqual(item["runtime"]["state"], "inactive")

        for capability, extension_id in (("image", "image"), ("screen", "companion.screen.native")):
            assembled = await command(extension_id, "assemble", 0, capability + ".assemble")
            self.assertEqual(assembled["status"], "ok", assembled)
            installation = assembled["data"]["installation"]
            self.assertEqual(installation["desired_state"], "active")
            self.assertEqual(installation["revision"], 1)
            await self.controller.native_extensions._lease(extension_id).start()
            running = next(item for item in (await self.request(manifest_path))["data"]["extensions"]
                           if item["capability_id"] == capability)
            self.assertTrue(running["runtime_ready"])
            self.assertEqual(running["runtime"]["state"], "running")
            replay = await command(extension_id, "assemble", 0, capability + ".assemble")
            self.assertTrue(replay["data"]["receipt"]["replayed"])

            if capability == "image":
                paused = await command(extension_id, "pause", 1, "image.pause")
                self.assertEqual(paused["data"]["installation"]["desired_state"], "paused")
                resumed = await command(extension_id, "resume", 2, "image.resume")
                self.assertEqual(resumed["data"]["installation"]["desired_state"], "active")
                uninstall_revision = resumed["data"]["installation"]["revision"]
            else:
                uninstall_revision = installation["revision"]

            removed = await command(extension_id, "uninstall", uninstall_revision, capability + ".uninstall")
            self.assertEqual(removed["status"], "ok", removed)
            retained = removed["data"]["installation"]
            self.assertEqual(retained["desired_state"], "discarded")
            retained_owner = "image.native" if capability == "image" else "screen"
            self.assertIn(retained_owner, retained["retained_dataset_owners"])

            provider = next(item for item in (await self.request(manifest_path))["data"]["extensions"]
                            if item["capability_id"] == capability)
            self.assertFalse(provider["assembled"])
            self.assertEqual(provider["integration_state"], "unassembled")
            self.assertIn("数据保留", provider["reason"])

            reassembled = await command(extension_id, "assemble", retained["revision"], capability + ".reassemble")
            self.assertEqual(reassembled["status"], "ok", reassembled)
            self.assertEqual(reassembled["data"]["installation"]["desired_state"], "active")
            self.assertEqual(reassembled["data"]["installation"]["retained_dataset_owners"],
                             retained["retained_dataset_owners"])

    async def test_bundled_package_drift_updates_without_uninstalling_or_losing_data(self):
        await self.start_host()
        registry = self.controller.native_extensions
        declaration = next(item for item in registry.declarations()
                           if item.descriptor.package.extension_id == "companion.screen.native")
        current_package = declaration.descriptor.package
        repository = CapabilityRepository(self.controller.runtime)
        legacy = replace(current_package, package_version="1.9.9", artifact_hash="legacy-screen-artifact")
        repository.register_descriptor(legacy, registered_at=support.NOW).unwrap()
        installed = repository.acquire(extension_id=legacy.extension_id, package_id=legacy.package_id,
            package_version=legacy.package_version, expected_revision=0, observed_at=support.NOW).unwrap()

        manifest = (await self.request(PREFIX + "/integration/manifest"))["data"]
        provider = next(item for item in manifest["extensions"] if item["capability_id"] == "screen")
        self.assertFalse(provider["runtime_ready"])
        self.assertTrue(provider["update_available"])
        self.assertEqual(provider["version"], current_package.package_version)
        self.assertEqual(provider["installed_package_version"], "1.9.9")

        response = await self.request(PREFIX + "/integration/lifecycle", method="POST", body={
            "extension_id":"companion.screen.native", "action":"update",
            "expected_revision":installed.revision, "operation_key":"screen.sync-current"})
        self.assertEqual(response["status"], "ok", response)
        updated = response["data"]["installation"]
        self.assertEqual(updated["desired_state"], "active")
        self.assertEqual(updated["package_version"], current_package.package_version)
        self.assertIn("screen", updated["retained_dataset_owners"])
        self.assertEqual(updated["revision"], installed.revision + 1)
        await self.controller.native_extensions._lease("companion.screen.native").start()

        refreshed = (await self.request(PREFIX + "/integration/manifest"))["data"]
        provider = next(item for item in refreshed["extensions"] if item["capability_id"] == "screen")
        self.assertEqual(provider["integration_state"], "connected")
        self.assertTrue(provider["runtime_ready"])
        self.assertFalse(provider["update_available"])
        self.assertEqual(provider["installed_package_version"], current_package.package_version)

    async def test_assemble_enables_only_the_requesting_role(self):
        await self.start_host()
        response = await self.request(PREFIX + "/integration/lifecycle", method="POST", body={
            "extension_id": "companion.screen.native", "action": "assemble", "expected_revision": 0,
            "operation_key": "screen.assemble-role", "role_id": "role-a", "target_id": "first",
        })
        self.assertEqual(response["status"], "ok", response)
        selection = response["data"]["role_selection"]
        self.assertTrue(selection["enabled"])
        self.assertEqual((selection["role"]["subject_id"], selection["bot_id"]), ("role-a", "bot-a"))
        rows = self.controller.runtime.read_all(
            "SELECT role_id, bot_id, enabled FROM capability_role_selections WHERE extension_id = ?",
            ("companion.screen.native",),
        )
        self.assertEqual([(row["role_id"], row["bot_id"], row["enabled"]) for row in rows],
                         [("role-a", "bot-a", 1)])
        context = self.controller.authority.context("first", "role-a")
        projection = CapabilityRepository(self.controller.runtime).projection(
            extension_id="companion.screen.native",
            role=context.role_binding.role, scope=context.scope,
        ).unwrap()
        self.assertTrue(projection.role_selection.enabled)

    async def test_bundled_lifecycle_rejects_non_core_dashboard_user(self):
        await self.controller.initialize_profile()
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                        expected_revision=policy.revision, observed_at=support.NOW)
        response = await self.request(PREFIX + "/integration/lifecycle", method="POST", body={
            "extension_id":"image", "action":"assemble", "expected_revision":0,
            "operation_key":"unauthorized-assemble",
        })
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["message"], "core_access_required")
        self.assertEqual(CapabilityRepository(self.controller.runtime).installation(extension_id="image").failure.code,
                         "capability_not_installed")
