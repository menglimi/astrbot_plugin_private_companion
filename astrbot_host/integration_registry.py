"""Discover optional providers without importing plugins or reading their stores.

The only invoked contract is companion_v2_status (metadata only). A readable
provider status and its own execution readiness are deliberately separate.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from companion_v2.product import CORE_COMPONENTS, PRODUCT_DISPLAY_NAME, PRODUCT_ID, PRODUCT_VERSION
from companion_v2.capabilities.models import CapabilityDesiredState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.adapters.astrbot.memory_providers import ExternalMemoryProviderRegistry, PROVIDER_API_VERSION
from companion_v2.diagnostics.readonly_status import read_p6_readonly_status
from .memory_page_api import MemoryPageAPI
from .integration_catalog import BUNDLED_EXTENSIONS, EXTERNAL_PROVIDERS

# Deprecated import surface for older host tests/extensions. It contains only
# genuine external providers; bundled modules are never placed here.
OPTIONAL_PROVIDERS = EXTERNAL_PROVIDERS


STATUS_SCHEMA = "companion.provider-status.v1"


class IntegrationRegistry:
    def __init__(self, context: Any):
        self.context = context

    def snapshot(self, *, host_status=None, application=None, native_extensions=None,
                 terminated=False, native_issues=()):
        discovery = "available"
        try:
            stars = self.context.get_all_stars()
        except Exception:
            stars, discovery = (), "unavailable"
        installed = {}
        for star in stars:
            # Match exact directory metadata, never a display-name substring.
            identifier = getattr(star, "root_dir_name", None) or getattr(star, "name", None)
            installed.setdefault(identifier, []).append(star)
        extensions = [self._bundled_extension(spec, application, native_extensions, terminated)
                      for spec in BUNDLED_EXTENSIONS]
        providers = [self._provider(spec, installed.get(spec["plugin_id"], []), discovery, terminated)
                     for spec in OPTIONAL_PROVIDERS]
        conflicts = {plugin_id for plugin_id, code in native_issues if code == "capability_descriptor_conflict"}
        for item in (*extensions, *providers):
            identity = {item.get("plugin_id"), item.get("package_id"), item.get("extension_id")}
            if conflicts.intersection(value for value in identity if value):
                item.update(integration_state="degraded", execution_issue="capability_descriptor_conflict",
                    reason="能力声明与保留版本冲突，当前不能在对话中调用。请更新模块版本后重新确认启用。")
        host = dict(host_status or {})
        core = [{**item, "routes": list(item["routes"]), "owner": "companion_v2",
                 "implementation": "included", "runtime_state": getattr(application, "state", "unloaded")}
                for item in CORE_COMPONENTS]
        return {
            "product": {"id": PRODUCT_ID, "display_name": PRODUCT_DISPLAY_NAME, "version": PRODUCT_VERSION,
                        "release_channel": "integrated", "direct_replacement": False,
                        "data_policy": "V2 独立运行库；旧版资料尚未迁移。"},
            "host": host, "core": core, "extensions": extensions, "providers": providers,
            "discovery": discovery,
            "summary": {"core_count": len(core), "extension_count": len(extensions),
                        "provider_count": len(providers),
                        "connected_count": sum(p["status_connected"] for p in providers),
                        "extension_ready_count": sum(p["runtime_ready"] is True for p in extensions),
                        "installed_count": sum(p["installed"] for p in providers),
                        "missing_count": sum(p["integration_state"] == "missing" for p in providers),
                        "unassembled_extension_count": sum(
                            p["integration_state"] == "unassembled" for p in extensions)},
        }

    @staticmethod
    def _bundled_extension(spec, application, native_extensions, terminated):
        item = {**spec, "kind": "native_extension", "owner": "companion_v2",
                "host_plugin_id": "astrbot_plugin_private_companion", "assembled": False,
                "version": PRODUCT_VERSION, "status_connected": True, "enabled": False,
                "runtime_ready": False, "integration_state": "unassembled",
                "reason": "V2 本体拓展尚未装配", "pages": [], "update_available": False,
                "package_id": None, "installed_package_version": None}
        if terminated:
            item.update(integration_state="stopped", reason="V2 宿主已停止")
            return item
        extension_id = spec["extension_id"]
        # Lifecycle metadata is package-scoped.  A package may be loaded while
        # one or more of its actions are dormant (Image has no current action
        # before an output endpoint is configured), so action-level catalog
        # readiness must not make the package look missing.
        declarations = [entry[0] for entry in getattr(native_extensions, "_entries", {}).values()
                        if entry[0].descriptor.package.extension_id == extension_id]
        package = (native_extensions.current_package(extension_id)
                   if native_extensions is not None and callable(getattr(native_extensions, "current_package", None))
                   else None)
        current = package is not None
        runtime_status = native_extensions.status(extension_id) if native_extensions is not None else {"state": "unavailable"}
        item["runtime"] = runtime_status
        item["runtime_ready"] = current and runtime_status["state"] == "running"
        if package is not None:
            item["package_id"] = package.package_id
            item["version"] = package.package_version
        if not current:
            item.update(integration_state="degraded", reason="V2 拓展执行器未就绪")
            return item
        runtime = getattr(application, "runtime", None)
        if runtime is None:
            item.update(integration_state="unknown", reason="V2 数据运行库尚未就绪")
            return item
        installation = CapabilityRepository(runtime).installation(extension_id=extension_id)
        if not installation.is_success:
            if installation.failure.code != "capability_not_installed":
                item.update(integration_state="degraded", reason="拓展安装状态无法读取")
            return item
        installed = installation.unwrap()
        same_package = bool(package and (installed.package_id, installed.package_version,
            installed.artifact_hash) == (package.package_id, package.package_version,
            package.artifact_hash))
        item.update(assembled=installed.desired_state is not CapabilityDesiredState.DISCARDED,
                    enabled=installed.desired_state is CapabilityDesiredState.ACTIVE,
                    installed_revision=installed.revision, desired_state=installed.desired_state.value,
                    installed_package_version=installed.package_version)
        if installed.desired_state is CapabilityDesiredState.DISCARDED:
            item.update(integration_state="unassembled", reason="拓展已卸载；V2 数据保留，可重新装配")
        elif not same_package:
            update_available = installed.desired_state in (
                CapabilityDesiredState.ACTIVE, CapabilityDesiredState.PAUSED,
            )
            item.update(integration_state="degraded", update_available=update_available,
                reason="已装配内容与当前 V2 内置版不同；可同步内置版，拓展数据会保留")
        elif installed.desired_state is CapabilityDesiredState.ACTIVE:
            if runtime_status["state"] == "failed":
                item.update(integration_state="degraded", execution_issue=runtime_status.get("error_code"),
                            reason="模块启动失败，请检查该模块设置后保存并重试")
            elif runtime_status["state"] == "starting":
                item.update(integration_state="connected", reason="模块正在后台启动")
            elif getattr(application, "state", None) != "running":
                item.update(integration_state="stopped", reason="模块已装配，等待 V2 启动")
            else:
                item.update(integration_state="connected", reason="V2 原生模块已启用")
        elif installed.desired_state is CapabilityDesiredState.PAUSED:
            item.update(integration_state="disabled", reason="拓展已暂停")
        else:
            item.update(integration_state="degraded", reason="拓展正在移除")
        return item

    # Compatibility for code that called the old private helper directly.
    _bundled_provider = _bundled_extension

    @staticmethod
    def _provider(spec, matches, discovery, terminated):
        item = {**spec, "owner": "external", "installed": bool(matches), "activated": False,
                "version": None, "status_connected": False, "enabled": None, "runtime_ready": None,
                "execution_owner": spec["plugin_id"], "execution_integrated": False,
                "integration_state": "missing", "reason": "插件未安装", "pages": []}
        if discovery == "unavailable":
            item.update(integration_state="unknown", reason="宿主没有提供插件目录")
        if terminated:
            item.update(integration_state="stopped", reason="V2 宿主已停止")
            return item
        if not matches:
            return item
        if len(matches) != 1:
            item.update(integration_state="conflict", reason="检测到重复提供方，请重新加载插件")
            return item
        star = matches[0]
        item["dashboard_name"] = str(getattr(star, "name", None) or spec["plugin_id"])
        item["version"] = str(getattr(star, "version", "") or "unknown")[:80]
        item["activated"] = getattr(star, "activated", False) is True
        if not item["activated"]:
            item.update(integration_state="disabled", reason="插件已停用")
            return item
        for page in (getattr(star, "pages", None) or []):
            name = page.get("name") if isinstance(page, dict) else getattr(page, "name", None)
            if isinstance(name, str) and name and len(name) <= 128:
                item["pages"].append(name)
        # Read only packaged page entry names. Never inspect provider data roots.
        pages_root = Path(__file__).resolve().parents[2] / spec["plugin_id"] / "pages"
        try:
            if pages_root.is_dir():
                for page in sorted(pages_root.iterdir()):
                    if (page.name != "framework-preview-v1" and page.is_dir()
                            and page.resolve().is_relative_to(pages_root.resolve())
                            and (page / "index.html").is_file() and page.name not in item["pages"]):
                        item["pages"].append(page.name)
        except OSError:
            # A page directory may disappear during a provider update. Metadata
            # discovery remains useful even when its optional page list fails.
            pass
        # An inactive Star must not be invoked even when a stale instance remains.
        api = getattr(getattr(star, "star_cls", None), "extension_api", None)
        method = getattr(api, "companion_v2_status", None)
        if not callable(method) or inspect.iscoroutinefunction(method):
            item.update(integration_state="installed", reason="已安装；需要加载新版状态适配器")
            return item
        try:
            status = method()
            if inspect.isawaitable(status):
                if inspect.iscoroutine(status):
                    status.close()
                raise ValueError("asynchronous status is not supported")
            if (type(status) is not dict or status.get("schema") != STATUS_SCHEMA
                    or status.get("plugin_id") != spec["plugin_id"]
                    or type(status.get("enabled")) is not bool
                    or (status.get("runtime_ready") is not None and type(status["runtime_ready"]) is not bool)):
                raise ValueError("invalid status contract")
            # Only these typed scalar fields can cross into the manifest. No raw
            # configuration, private error text, paths, tokens or user data.
            item.update(status_connected=True, enabled=status["enabled"], runtime_ready=status.get("runtime_ready"))
            if status["enabled"] is False:
                item.update(integration_state="disabled", reason="提供方配置已关闭")
            elif status.get("runtime_ready") is False:
                item.update(integration_state="waiting", reason="状态已接入；提供方尚未就绪")
            else:
                item.update(integration_state="connected", reason="状态已接入；功能由原提供方执行")
        except Exception:
            item.update(integration_state="degraded", reason="提供方状态读取失败，可重新检查")
        return item


class UnifiedCompanionExtensionAPI:
    """Public extension facade; conversation/operation authority stays in the host."""
    api_version = "companion.product.v1"
    memory_provider_api_version = PROVIDER_API_VERSION

    def __init__(self, controller):
        self.controller = controller
        self._memory_page = MemoryPageAPI(controller)
        from .mobile_together import MobileTogetherBridge
        self._mobile_together = MobileTogetherBridge(controller)
        from .mobile_game import MobileGameBridge
        self._mobile_game = MobileGameBridge(controller)
        from .native_extensions.reality.host_context import RealityHostContext
        from .native_extensions.reality.host_delivery import RealityHostDelivery
        self._reality_context = RealityHostContext(controller, self._mobile_together)
        self._reality_delivery = RealityHostDelivery(self._reality_context)
        self._memory_providers = ExternalMemoryProviderRegistry(controller.context,
            host_active=lambda: not getattr(controller, "_terminated", False))

    def register_memory_provider(self, provider_id, *, plugin, read=None, write=None, summary=None,
                                 display_name="", priority=0, handles_model_memory=False,
                                 is_active=None, timeout_seconds=5.0):
        """Register callbacks owned by a live Star; return an opaque removal token."""
        if getattr(self.controller, "_terminated", False):
            raise ValueError("memory provider host is stopped")
        return self._memory_providers.register(provider_id, plugin=plugin, read=read, write=write,
            summary=summary, display_name=display_name, priority=priority,
            handles_model_memory=handles_model_memory, is_active=is_active, timeout_seconds=timeout_seconds)

    def unregister_memory_provider(self, registration_id):
        return self._memory_providers.unregister(registration_id)

    def list_memory_providers(self):
        return self._memory_providers.snapshot()

    def close_memory_providers(self):
        self._memory_providers.close()

    async def generate_reality_touch_text(self, prompt, **kwargs):
        return await self._reality_context.generate(prompt, **kwargs)

    async def get_realtime_context(self, user_id, **kwargs):
        return await self._reality_context.realtime(user_id, **kwargs)

    async def get_scene_context(self, user_id, **kwargs):
        return await self._reality_context.realtime(user_id, **kwargs)

    async def send_reality_touch_chat(self, umo, text, **kwargs):
        return await self._reality_delivery.send(umo, text, **kwargs)

    async def record_reality_touch_output(self, user_id, text, **kwargs):
        return await self._reality_delivery.record(user_id, text, **kwargs)

    def get_reality_touch_cron_manager(self):
        return self._reality_context.cron()

    async def delete_reality_touch_cron_job(self, job_id):
        return await self._reality_context.delete_job(job_id)

    async def synthesize_realtime_voice(self, text, **kwargs):
        return await self._reality_context.synthesize(text, **kwargs)

    async def notify_mobile_location_update(self, user_id):
        return await self._reality_delivery.location(user_id)

    async def mobile_location_diagnostic_candidate(self, user_id, *, test_id, diagnostic_origin):
        return await self._reality_delivery.location_diagnostic_candidate(user_id,
            test_id=test_id, diagnostic_origin=diagnostic_origin)

    def mobile_target_context(self, user_id):
        return self._mobile_together._target_context(user_id)

    def mobile_pairing_candidates(self):
        return self._mobile_together.pairing_candidates()

    def mobile_core_pairing_candidates(self):
        return self._mobile_together.core_pairing_candidates()

    def reality_mobile_context(self, user_id):
        """Return the verified V2 identity used by the native Reality gateway.

        The mobile bridge may retain this attestation for a session, but it
        must resolve the exact active private target every time it is checked.
        No legacy plugin object or mutable user record crosses this boundary.
        """
        if type(user_id) is not str or not user_id.strip() or len(user_id) > 160:
            return None
        try:
            resolved = self.mobile_target_context(user_id.strip())
        except Exception:
            return None
        if not isinstance(resolved, tuple) or len(resolved) != 3:
            return None
        _session, application, context = resolved
        scope = getattr(context, "scope", None)
        actor = getattr(context, "actor", None)
        principal = getattr(actor, "principal", None)
        subject = getattr(actor, "subject", None)
        if (getattr(scope, "kind", None) is None
                or getattr(scope.kind, "value", None) != "private"
                or getattr(scope, "native_scope_id", None) != user_id.strip()
                or getattr(principal, "native_account_id", None) != user_id.strip()
                or not getattr(subject, "subject_id", None)):
            return None
        registry = getattr(self.controller, "native_extensions", None)
        snapshot_authorized = False
        camera_authorized = False
        if registry is not None and getattr(registry, "application", None) is application:
            external = getattr(application, "external", None)
            if external is not None:
                try:
                    actions = {tool.action for tool in external.catalog_for(context)}
                    snapshot_authorized = "reality.snapshot.read" in actions
                    camera_authorized = "reality.camera.observe" in actions
                except Exception:
                    pass
        return {
            "schema": "companion.reality-mobile-context.v1",
            "user_id": user_id.strip(),
            "context_key": getattr(context, "key", ""),
            "scope_key": getattr(scope, "key", ""),
            "subject_ref": f"{getattr(subject.kind, 'value', 'subject')}:{subject.subject_id}",
            "snapshot_authorized": snapshot_authorized,
            "camera_authorized": camera_authorized,
        }

    def companion_v2_mobile_game_status(self, session_token):
        return self._mobile_game.status(session_token)

    async def companion_v2_mobile_game_create(self, session_token, request_id, game_type,
                                              *, via_mobile_gateway=False):
        return await self._mobile_game.create(session_token, request_id, game_type,
                                             via_mobile_gateway=via_mobile_gateway)

    def companion_v2_mobile_session_user(self, session_token: str) -> dict[str, str]:
        return self._mobile_together.mobile_session_user(session_token)

    async def companion_v2_mobile_reality_snapshot(self, session_token: str):
        return await self._reality_context.mobile_snapshot(session_token)

    def companion_v2_mobile_together_status(self, session_token: str, *, via_mobile_gateway: bool = False):
        return self._mobile_together.status(session_token)

    async def companion_v2_mobile_together_create(self, session_token: str, request_id: str, mode: str,
                                                  *, via_mobile_gateway: bool = False):
        result = await self._mobile_together.invoke(session_token, request_id=request_id,
                                                    operation="create", arguments={"mode": mode})
        return self._mobile_together.mobile_result("create", result)

    async def companion_v2_mobile_together_invite(self, session_token: str, request_id: str,
                                                  expires_in_seconds: int | None = None,
                                                  *, via_mobile_gateway: bool = False):
        arguments = {} if expires_in_seconds is None else {"expires_in_seconds": expires_in_seconds}
        result = await self._mobile_together.invoke(session_token, request_id=request_id,
                                                    operation="invite", arguments=arguments)
        return self._mobile_together.mobile_result("invite", result)

    async def companion_v2_mobile_together_join(self, session_token: str, request_id: str,
                                                invitation_id: str,
                                                *, via_mobile_gateway: bool = False):
        result = await self._mobile_together.invoke(session_token, request_id=request_id,
            operation="join", arguments={"invitation_id": invitation_id})
        return self._mobile_together.mobile_result("join", result)

    def manifest(self):
        controller = self.controller
        session = controller.session or controller.last_session
        return controller.integration.snapshot(host_status=controller.status(),
            application=session.application if session else None, terminated=controller._terminated,
            native_extensions=getattr(controller, "native_extensions", None),
            native_issues=getattr(getattr(controller, "native_extensions", None), "issues", ()))

    def status(self):
        return self.manifest()

    def get_p6_readonly_status(self):
        """Expose bounded registry counts to the memory-companion P6 bridge."""
        runtime = getattr(self.controller, "runtime", None)
        if runtime is None:
            session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
            runtime = getattr(getattr(session, "application", None), "runtime", None)
        return read_p6_readonly_status(runtime)

    def memory_page_capabilities(self):
        """Publish the formal memory-page descriptor without host internals."""
        return self._memory_page.capabilities()

    async def export_memory_page_snapshot(self, *, target_plugin_id="", selected_date=""):
        return await self._memory_page.export_snapshot(
            target_plugin_id=target_plugin_id, selected_date=selected_date)

    async def read_memory_page_photo(self, *, target_plugin_id="", photo_ref=""):
        return await self._memory_page.read_photo(
            target_plugin_id=target_plugin_id, photo_ref=photo_ref)

    def capabilities(self):
        return {"api_version": self.api_version, "product_id": PRODUCT_ID,
                "implemented_domains": [item["id"] for item in CORE_COMPONENTS],
                "lifecycle_state": "stopped" if self.controller._terminated else "ready"}

    def room_furniture_catalog(self):
        manifest = self.manifest()
        items = [{**item["furniture"],
                  "plugin_id": "astrbot_plugin_private_companion",
                  "extension_id": item["extension_id"],
                  "integration_state": item["integration_state"],
                  "owner": "companion_v2"}
                 for item in manifest.get("extensions", [])]
        return {"items": items}
