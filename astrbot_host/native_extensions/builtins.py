"""Native capability modules owned and packaged by the single V2 plugin."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import logging
from pathlib import Path

from companion_v2.capabilities.models import FurnitureDescriptor
from companion_v2.capabilities.native import parse_manifest
from companion_v2.product import PRODUCT_VERSION

from .discovery import declarations_for_manifest
from .image_native.adapter import PLUGIN_ID as IMAGE_PACKAGE_ID
from .image_native.extension import ImageExtensionAPI
from .image_native.package import package_hash as image_package_hash
from .screen_provider import NativeScreenProvider, PLUGIN_ID as SCREEN_PACKAGE_ID, screen_package_hash
from .settings import ExtensionSettings


IMAGE_EXTENSION_ID = "image"
SCREEN_EXTENSION_ID = "companion.screen.native"
LEGACY_SCREEN_EXTENSION_ID = "companion.screen"
MANAGED_EXTENSIONS = frozenset({"content", "reality", "together", "game", "live",
    IMAGE_EXTENSION_ID, SCREEN_EXTENSION_ID, LEGACY_SCREEN_EXTENSION_ID})


class BundledNativeLease:
    def __init__(self, package_id, port, *, api=None, furniture, factory=None, package_version=PRODUCT_VERSION):
        self.port = port
        self.api = api
        self.star = None
        self.instance = api or port
        self._closed = False
        self._close_task = None
        self._start_task = None
        self._disposed = False
        self.factory = factory
        self.state = "inactive"
        self.error_code = None
        # The manifest is the immutable catalog identity of this session.  Do
        # not put mutable per-request configuration (for example an image
        # endpoint timeout) in it: executors pin these declarations, persist
        # their encoding with every invocation and compare it on replay, so a
        # later "refresh" would invalidate running and recorded work.  Providers
        # resolve live budgets themselves; the declaration carries only the
        # documented ceiling.
        manifest = parse_manifest(port.manifest(), plugin_id=package_id, version=package_version)
        package = replace(manifest.package, furniture=(furniture,))
        self.manifest = replace(manifest, package=package)

    def current(self, _context):
        return not self._closed and not getattr(self.port, "_closed", False) and not getattr(self.port, "closed", False)

    def declarations(self):
        return declarations_for_manifest(self.manifest)

    async def start(self):
        if self._closed or self.state == "running":
            return
        if self._start_task is None:
            self.state = "starting"
            self._start_task = asyncio.create_task(self._initialize(), name="companion-v2:extension-start")
        await self._start_task

    async def _initialize(self):
        try:
            method = getattr(self.instance, "initialize", None)
            if callable(method):
                await method()
            if not self._closed:
                self.state, self.error_code = "running", None
        except asyncio.CancelledError:
            raise
        except Exception:
            self.state, self.error_code = "failed", "native_service_start_failed"
            logging.getLogger(__name__).exception("V2 native startup failed: %s", self.manifest.package.extension_id)

    def begin_start(self):
        if not self._closed and self._start_task is None:
            self.state = "starting"
            self._start_task = asyncio.create_task(self._initialize(), name="companion-v2:extension-start")

    async def aclose(self):
        self._closed = True
        self.state = "inactive"
        current = asyncio.current_task()
        if self._close_task is not None and self._close_task is not current:
            await asyncio.shield(self._close_task)
            return
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        if self._disposed:
            return
        self._disposed = True
        method = getattr(self.instance, "terminate", None)
        if callable(method):
            await method()
        else:
            method = getattr(self.api, "close", None) or getattr(self.port, "close", None)
            if callable(method):
                result = method()
                if inspect.isawaitable(result):
                    await result

    def close(self):
        if self._close_task is not None:
            return
        self._closed = True
        self.state = "inactive"
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._close_task = loop.create_task(self.aclose(), name="companion-v2:extension-close")


def create_bundled_extension(data_root, name, *, now=None, context=None, controller=None):
    root = Path(data_root).resolve()
    factory = lambda: create_bundled_extension(root, name, now=now, context=context, controller=controller)
    if name == "content":
        from companion_v2.content.assembly import build_provider
        from companion_v2.content.provider import PLUGIN_ID
        port = build_provider(root / "extensions" / name, version=PRODUCT_VERSION, live=lambda: None)
        return BundledNativeLease(PLUGIN_ID, port, factory=factory,
            furniture=FurnitureDescriptor(name, name, "创作书架", name))
    if name == "image":
        # Rebuilt transports can differ within one public release. Keep each
        # catalog descriptor immutable without making a normal rebuild a conflict.
        # Use the same identity for provider requests and persisted declarations.
        version = PRODUCT_VERSION + "+image." + image_package_hash()
        api = ImageExtensionAPI(data_root=root, config_path=root / "extensions" / name / "config.json",
                                version=version, context=context)
        return BundledNativeLease(IMAGE_PACKAGE_ID, api.companion_v2_native(), api=api, factory=factory,
            package_version=version,
            furniture=FurnitureDescriptor(name, IMAGE_EXTENSION_ID, "画面工作室", name))
    if name == "screen":
        version = PRODUCT_VERSION + "+screen." + screen_package_hash()[:12]
        port = NativeScreenProvider(root / "extensions" / name, version=version, now=now)
        return BundledNativeLease(SCREEN_PACKAGE_ID, port, factory=factory, package_version=version,
            furniture=FurnitureDescriptor(name, SCREEN_EXTENSION_ID, "屏幕台", name))
    from importlib import import_module
    labels = {"reality": "现实设备台", "together": "共处电话", "game": "游戏棋桌", "live": "直播与表演"}
    config = ExtensionSettings(root, name)
    module = import_module("." + name + ".runtime", __package__)
    runtime = getattr(module, name.title() + "Runtime")(context, config, config.root, controller)
    return BundledNativeLease("astrbot_plugin_private_companion." + name,
        runtime.build_native(PRODUCT_VERSION), api=runtime, factory=factory,
        furniture=FurnitureDescriptor(name, name, labels[name], name))


def create_bundled_extensions(data_root: Path, *, now=None, context=None, controller=None):
    root = Path(data_root).resolve()
    leases, issues = [], []
    names = ["content", "image", "screen"]
    if context is not None and controller is not None:
        names.extend(("reality", "together", "game", "live"))
    for name in names:
        try:
            leases.append(create_bundled_extension(root, name, now=now, context=context, controller=controller))
        except Exception as error:
            code = getattr(error, "code", "")
            safe_code = (code if name == "image" and isinstance(code, str) and code.isascii()
                         and len(code) <= 80 and code.replace("_", "").isalnum()
                         else "native_extension_unavailable")
            issues.append(("astrbot_plugin_private_companion." + name, safe_code))
            logging.getLogger(__name__).exception("V2 native module unavailable: %s", name)
    return tuple(leases), tuple(issues)
