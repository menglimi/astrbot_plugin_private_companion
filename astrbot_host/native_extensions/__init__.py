"""V2 native execution through pinned AstrBot ports, JSON and verified bytes."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import json
import logging
import traceback
from types import MappingProxyType
from typing import Callable, Mapping

from companion_v2.adapters.astrbot.tools import AstrBotToolPort, map_tools
from companion_v2.capabilities.native import invocation_request, json_document, parse_result
from companion_v2.capabilities.native_reads import READ_ACTIONS, NativeReadConsumer
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.capabilities.models import CapabilityDesiredState
from companion_v2.conversation.external_tools import ExternalToolResponse
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.sources.models import GovernanceState, SourceRecord

logger = logging.getLogger(__name__)

from .discovery import discover
from .images import IMAGE_ACTIONS, ImageReferenceLease, image_result_handler
from .image_settings import read_image_settings
from .content import ACTIONS as CONTENT_ACTIONS, ContentConsumer
from .screens import ACTION as SCREEN_ACTION, ScreenAdopter
from .reality_camera import ACTION as REALITY_CAMERA_ACTION, RealityCameraConsumer, RealityCameraGuard


TOGETHER_CREATE_ACTION = "together.room.create"


def together_execution_binding(settings):
    voice = settings.voice_reply_policy
    return {"model_provider_id": settings.provider_id,
            "tts_provider_id": voice.provider_for_language(),
            "tts_language": voice.language,
            "tts_timeout_seconds": voice.timeout_seconds}


def together_room_execution(binding, service):
    """Pin explicit room model choices while retaining the verified host defaults."""
    execution = dict(binding)
    for target, setting in (("model_provider_id", "chat_provider_id"),
                            ("tts_provider_id", "tts_provider_id")):
        selected = str(getattr(service, setting, "") or "").strip()
        if selected:
            execution[target] = selected
    timeout = getattr(service, "tts_timeout_seconds", None)
    if type(timeout) is int and 15 <= timeout <= 180:
        execution["tts_timeout_seconds"] = timeout
    return execution


class NativeExtensionRegistry(AstrBotToolPort):
    """Discovery never mutates storage; execution borrows the original task fence."""

    def __init__(self, context, *, result_handlers: Mapping[str, Callable] | None = None, now=None,
                 together_execution=None, image_settings_path=None, bundled_extensions=(),
                 bundled_issues=(), managed_extensions=()):
        self.context = context
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.together_execution = dict(together_execution or {})
        self._default_handlers = result_handlers is None
        if result_handlers is None:
            result_handlers = {action: lambda invocation, result: image_result_handler(
                invocation, result, observed_at=self.now()) for action in IMAGE_ACTIONS}
            # Read-only providers (Reality, Live) have no artifact to adopt, but their
            # results still carry domain obligations: no credential may reach a model,
            # and a live cursor must advance instead of replaying delivered events.
            reader = NativeReadConsumer(self)
            result_handlers.update({action: reader.adopt for action in READ_ACTIONS})
            camera_reader = RealityCameraConsumer(self)
            result_handlers[REALITY_CAMERA_ACTION] = camera_reader.adopt
        self.result_handlers = MappingProxyType(dict(result_handlers))
        self.application = self.runtime = self.role_guard = self.persona_guard = None
        self.content = None
        self.screen = None
        self.camera = None
        self._entries, discovered_issues = discover(context, excluded_extensions=managed_extensions)
        self.managed_extensions = frozenset(managed_extensions)
        self._bundled_extensions = tuple(bundled_extensions)
        for lease in self._bundled_extensions:
            self._entries.update({item.host_tool_id: (item, lease) for item in lease.declarations()})
        self.issues = tuple((*discovered_issues, *bundled_issues))
        self._blocked_extension_ids = frozenset()
        self.image_settings_path = image_settings_path
        self.image_enabled = read_image_settings(image_settings_path)
        self._enforcing_image_settings = False
        self._closed = False
        self._start_task = None
        self._services_lock = asyncio.Lock()

    def begin_start(self):
        if not self._closed and (self._start_task is None or self._start_task.done()):
            self._start_task = asyncio.create_task(self.start(), name="companion-v2:native-services")

    def _lease(self, extension_id):
        return next((lease for lease in self._bundled_extensions
                     if lease.manifest.package.extension_id == extension_id), None)

    def current_package(self, extension_id):
        """Return the loaded package even when one of its actions is dormant.

        A native package can be present while an individual action is withheld
        by its own backend settings (for example Image before an endpoint is
        configured).  Lifecycle management operates on the package identity;
        invocation catalogues operate on action readiness.
        """
        lease = self._lease(extension_id)
        if (self._closed or lease is None
                or extension_id in self._blocked_extension_ids
                or not lease.current(self.context)):
            return None
        return lease.manifest.package

    def api(self, extension_id):
        for lease in self._bundled_extensions:
            if lease.manifest.package.extension_id == extension_id and lease.current(self.context):
                return getattr(lease.instance, "extension_api", None) or lease.api or lease.port
        return None

    def service(self, extension_id):
        for lease in self._bundled_extensions:
            if lease.manifest.package.extension_id == extension_id and lease.current(self.context):
                return lease.instance
        return None

    def active_service(self, extension_id):
        lease = self._lease(extension_id)
        if lease is not None and self._should_start(lease) and lease.state == "running":
            return self.service(extension_id)
        return None

    def status(self, extension_id):
        lease = self._lease(extension_id)
        return {"state": lease.state if lease is not None else "unavailable",
                "error_code": lease.error_code if lease is not None else "native_extension_unavailable"}

    def _should_start(self, lease):
        if (self._closed or self.runtime is None or getattr(self.application, "state", None) != "running"
                or lease.manifest.package.extension_id in self._blocked_extension_ids):
            return False
        result = CapabilityRepository(self.runtime).installation(extension_id=lease.manifest.package.extension_id)
        if not result.is_success:
            return False
        installation, package = result.unwrap(), lease.manifest.package
        return installation.desired_state is CapabilityDesiredState.ACTIVE and (
            installation.package_id, installation.package_version, installation.artifact_hash) == (
            package.package_id, package.package_version, package.artifact_hash)

    async def _replace_service(self, lease):
        if lease.factory is None:
            return lease
        # Stop before rebuilding to release ports, save domain state and ensure
        # an old provider can never adopt a replacement's result.
        lease.close()
        if lease._close_task is not None:
            await lease._close_task
        else:
            await lease.aclose()
        replacement = lease.factory()
        if replacement.manifest != lease.manifest:
            await replacement.aclose()
            raise TaskError("native_settings_catalog_changed", "the capability catalog changed; reload the V2 host")
        self._entries.update({entry.host_tool_id: (entry, replacement) for entry in replacement.declarations()})
        self._bundled_extensions = tuple(replacement if item is lease else item for item in self._bundled_extensions)
        return replacement

    async def reconcile_service(self, extension_id):
        async with self._services_lock:
            lease = self._lease(extension_id)
            if lease is None or self._closed:
                return
            if self._should_start(lease):
                if not lease.current(self.context) or lease.state == "failed":
                    lease = await self._replace_service(lease)
                lease.begin_start()
            elif lease.state != "inactive":
                await self._replace_service(lease)

    async def configure_service(self, extension_id, updates, revision):
        async with self._services_lock:
            lease = self._lease(extension_id)
            if self._closed or lease is None or extension_id not in ("reality", "together", "game", "live"):
                raise TaskError("native_settings_unavailable", "this extension has its own settings page")
            settings = lease.instance.config
            if revision != settings.revision():
                raise TaskError("native_settings_conflict", "extension settings changed; reload before saving")
            settings.update_fields(updates)
            replacement = await self._replace_service(lease)
            if self._should_start(replacement):
                replacement.begin_start()
            return {"extension_id": extension_id, "settings": settings.public(), "schema": settings.schema,
                    "revision": settings.revision(), "runtime": self.status(extension_id), "saved": True}

    async def start(self):
        for lease in self._bundled_extensions:
            if self._closed:
                break
            self._ensure_installation(lease)
            if self._should_start(lease):
                lease.begin_start()
        # 目录/准入排查用：一次打印自带的模块、声明数量与状态。
        summary = []
        for lease in self._bundled_extensions:
            extension_id = lease.manifest.package.extension_id
            declared = sum(1 for declaration in lease.declarations())
            summary.append(f"{extension_id}:{declared}:{lease.state}:"
                           f"{'blocked' if extension_id in self._blocked_extension_ids else 'ok'}")
        logger.info("[CompanionV2][native] bundled=%s issues=%s image_enabled=%s",
                    ",".join(summary), list(self.issues), self.image_enabled)

    # 只在插件自带的「生图」模块上自动补齐：它的接口配置已经存在，缺的只是装配记录。
    # 其他模块仍由用户显式装配，避免悄悄扩大能力授权。
    AUTO_INSTALL_EXTENSIONS = frozenset({"image"})

    def _ensure_installation(self, lease):
        """随插件一起发货的原生模块，配置好就应该能用。

        能力安装记录只由「装配」动作创建；如果用户只是在小屋里填好生图接口，
        安装记录一直缺着，模块永远不会启动，模型也就拿不到生图工具。这里对
        插件自带的生图模块做一次幂等补齐（已安装或未启用时什么都不做）。
        """
        extension_id = lease.manifest.package.extension_id
        if (self._closed or self.runtime is None or extension_id not in self.managed_extensions
                or extension_id not in self.AUTO_INSTALL_EXTENSIONS):
            return
        if extension_id == "image" and self.image_enabled is False:
            return
        repository = CapabilityRepository(self.runtime)
        installed = repository.installation(extension_id=extension_id)
        if installed.is_success:
            return
        if installed.failure is not None and installed.failure.code != "capability_not_installed":
            return
        package = lease.manifest.package
        observed_at = self.now()
        registered = repository.register_descriptor(package, registered_at=observed_at)
        if registered.failure is not None and registered.failure.code not in ("capability_descriptor_conflict",
                                                                             "capability_identity_conflict"):
            logging.getLogger(__name__).warning(
                "[CompanionV2] 原生模块装配登记失败 extension=%s code=%s",
                extension_id, registered.failure.code)
            return
        acquired = repository.acquire(extension_id=extension_id, package_id=package.package_id,
            package_version=package.package_version, expected_revision=0, observed_at=observed_at)
        if acquired.failure is not None:
            logging.getLogger(__name__).warning(
                "[CompanionV2] 原生模块自动装配失败 extension=%s code=%s",
                extension_id, acquired.failure.code)

    async def aclose(self):
        self.close()
        tasks = [lease._close_task for lease in self._bundled_extensions if lease._close_task is not None]
        tasks.extend(lease.aclose() for lease in self._bundled_extensions if lease._close_task is None)
        if self._start_task is not None:
            tasks.append(self._start_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def declarations(self):
        return tuple(entry[0] for entry in self._entries.values()
                     if entry[0].descriptor.package.extension_id not in self._blocked_extension_ids) if not self._closed else ()

    def current_declaration(self, host_tool_id):
        if self.declaration_readiness(host_tool_id) != "ready":
            return None
        return self._entries[host_tool_id][0]

    def declaration_readiness(self, host_tool_id):
        """Explain whether a loaded declaration can currently be invoked."""
        entry = self._entries.get(host_tool_id)
        if (self._closed or entry is None
                or entry[0].descriptor.package.extension_id in self._blocked_extension_ids
                or not entry[1].current(self.context)):
            return "stale"
        if (self._enforcing_image_settings and entry[0].descriptor.tool.action in IMAGE_ACTIONS
                and not self.image_enabled[entry[0].descriptor.tool.action]):
            return "disabled"
        if self._enforcing_image_settings and entry[0].descriptor.tool.action in IMAGE_ACTIONS:
            action = entry[0].descriptor.tool.action
            backend = getattr(getattr(entry[1].port, "owner", None), "backend", None)
            operations = getattr(backend, "operations", None)
            if operations is not None and action.split(".", 1)[1] not in operations:
                return "unsupported"
        return "ready"

    def image_extension_api(self):
        "The management API pinned to a current V2-native Image declaration."
        if self._closed:
            return None
        for declaration, lease in self._entries.values():
            if declaration.descriptor.tool.action in IMAGE_ACTIONS and lease.current(self.context):
                return lease.api
        return None

    def set_image_enabled(self, settings):
        if self._closed or set(settings) != IMAGE_ACTIONS or any(type(value) is not bool for value in settings.values()):
            raise ValueError("image settings require both native actions")
        self.image_enabled = dict(settings)

    def attach(self, application):
        runtime = getattr(application, "runtime", None)
        if runtime is None or self._closed or (self.application is not None and self.application is not application):
            raise ValueError("native registry must attach to one V2 application")
        self.application, self.runtime = application, runtime
        # The real V2 application always exposes ``tasks.identity``.  Keep
        # registry attachment tolerant of a lightweight host fixture (or a
        # partially constructed application) and defer the Screen consumer
        # until the authorization service is available.
        tasks = getattr(application, "tasks", None)
        if (self.screen is None and tasks is not None and getattr(tasks, "identity", None) is not None
                and any(entry[0].descriptor.tool.action == SCREEN_ACTION for entry in self._entries.values())):
            self.screen = ScreenAdopter(self, application)
        if self.camera is None and any(entry[0].descriptor.tool.action == REALITY_CAMERA_ACTION
                                       for entry in self._entries.values()):
            self.camera = RealityCameraGuard(self, application)
        if (self.content is None and getattr(application, "messages", None) is not None
                and any(entry[0].descriptor.tool.action in CONTENT_ACTIONS for entry in self._entries.values())):
            self.content = ContentConsumer(self, application)
        return self

    def bind_role_guard(self, guard, persona_guard=None):
        if (self._closed or not inspect.iscoroutinefunction(guard)
                or (persona_guard is not None and not inspect.iscoroutinefunction(persona_guard))):
            raise ValueError("native role attestation requires a cooperative host bridge")
        self.role_guard = guard
        self.persona_guard = persona_guard

    def register_catalog(self, *, observed_at):
        """Trusted management operation; declaration registration grants no use rights."""
        if self.runtime is None or self._closed:
            return Outcome.failed("native_registry_unattached", "attach the current registry to a V2 runtime first")
        try:
            repository = CapabilityRepository(self.runtime)
            blocked = {}
            with self.runtime.transaction():
                packages = {}
                for declaration in self.declarations():
                    package = declaration.descriptor.package
                    if package.extension_id in blocked:
                        continue
                    if self.current_declaration(declaration.host_tool_id) != declaration:
                        raise TaskError("native_provider_changed", "native declaration is no longer current")
                    if package.extension_id not in packages and package.package_id.startswith("astrbot_plugin_private_companion."):
                        from companion_v2.capabilities.native_catalog import register_native_replacement
                        predecessors = {"content": "astrbot_plugin_content_companion", "image": "astrbot_plugin_image_companion",
                            "reality": "astrbot_plugin_reality_companion", "together": "astrbot_plugin_together_companion",
                            "game": "astrbot_plugin_game_companion", "live": "astrbot_plugin_live_stream_companion",
                            "companion.screen.native": package.package_id}
                        previous = predecessors.get(package.extension_id)
                        if previous is not None:
                            # Bundled modules can already have a V2 owner without
                            # ever having installed the independent predecessor.
                            # Advance that pinned version too, retaining its state
                            # and existing grants rather than silently hiding it.
                            for predecessor in dict.fromkeys((previous, package.package_id)):
                                upgraded = register_native_replacement(self.runtime, package,
                                    previous_package_id=predecessor, observed_at=observed_at)
                                if not upgraded.is_success:
                                    blocked[package.extension_id] = (package.package_id, upgraded.failure.code)
                                    break
                            if package.extension_id in blocked:
                                continue
                    registered = repository.register_descriptor(package, registered_at=observed_at)
                    if registered.failure is not None and registered.failure.code in {
                            "capability_descriptor_conflict", "capability_identity_conflict"}:
                        # An optional provider cannot replace an immutable package
                        # identity or take the whole core offline.
                        # Retain its records and refuse every tool from this extension.
                        blocked[package.extension_id] = (
                            self._entries[declaration.host_tool_id][1].manifest.plugin_id,
                            registered.failure.code,
                        )
                        continue
                    packages[package.extension_id] = require(registered)
                if self.content is not None and "content" not in blocked and "content" not in self._blocked_extension_ids:
                    self.content.sources.synchronize()
            self._blocked_extension_ids = self._blocked_extension_ids | frozenset(blocked)
            self.issues = tuple(dict.fromkeys((*self.issues, *blocked.values())))
            self._enforcing_image_settings = True
            return Outcome.success(tuple(packages.values()))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logging.getLogger(__name__).error("[Companion] 原生能力目录注册异常 error_type=%s\n%s",
                type(error).__name__, "".join(traceback.format_tb(error.__traceback__)))
            return Outcome.failed("native_catalog_registration_failed", "native catalog registration failed")

    def _guard(self, host_tool_id, invocation):
        if self.current_declaration(host_tool_id) is None:
            raise TaskError("native_provider_changed", "the pinned native provider is no longer loaded")
        lease = self._entries[host_tool_id][1]
        if callable(getattr(lease, 'factory', None)) and lease.state != 'running':
            raise TaskError('native_service_not_running', '此 V2 模块尚未启动完成，请稍后再调用该功能')
        if self.now() >= invocation.deadline_at:
            raise TaskError("native_deadline_expired", "the original native request has expired")
        if self.application is not None:
            self.application.require_running()
            if self.application.external is None or self.application.external.providers.get(
                    invocation.descriptor.tool.action) is None:
                raise TaskError("native_executor_unavailable", "this application did not assemble the native executor")
            self.application.external._admit(invocation.descriptor, invocation.context, self.now())
        if not callable(invocation.before_dispatch) or inspect.iscoroutinefunction(invocation.before_dispatch):
            raise TaskError("native_guard_missing", "native execution requires the original synchronous task fence")
        result = invocation.before_dispatch()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TaskError("native_guard_invalid", "native execution cannot defer authorization")

    async def _role(self, host_tool_id, invocation):
        self._guard(host_tool_id, invocation)
        if self.role_guard is not None:
            await self.role_guard(invocation.context.scope, invocation.context.role_binding)
            self._guard(host_tool_id, invocation)

    async def invoke(self, host_tool_id, invocation):
        entry = self._entries.get(host_tool_id)
        if entry is None or entry[0].descriptor != invocation.descriptor:
            raise TaskError("native_provider_changed", "the original native declaration is unavailable")
        port = entry[1].port

        def guard():
            if self._entries.get(host_tool_id) is not entry:
                raise TaskError("native_provider_changed", "the original native service was restarted")
            self._guard(host_tool_id, invocation)

        await self._role(host_tool_id, invocation)
        guard()
        action = invocation.descriptor.tool.action
        reference_lease = None
        if action == "image.mask_edit" or (action == "image.edit"
                and "artifact_id" not in json.loads(invocation.arguments_json)):
            reference_lease = ImageReferenceLease(self, invocation)
        screen_check = self.screen.preflight(invocation) if self.screen is not None and invocation.descriptor.tool.action == SCREEN_ACTION else None
        camera_check = None
        if self.camera is not None and action == REALITY_CAMERA_ACTION:
            try:
                camera_check = self.camera.preflight(invocation)
            except TaskError as error:
                # This host denial happens before port.execute can capture a
                # frame. Keep its own receipt instead of poisoning later valid
                # camera requests with an unknown device effect.
                receipt_ref = "astrbot.camera-preflight." + invocation.invocation_id
                document = {"invocation_id": invocation.invocation_id, "origin_key": invocation.context.key,
                    "arguments_hash": invocation.arguments_hash, "binding": invocation.descriptor.binding,
                    "state": "failed", "effect": "not_committed", "code": error.code,
                    "observed_at": self.now()}
                with self.runtime.transaction() as connection:
                    connection.execute("INSERT OR IGNORE INTO runtime_metadata (key,value) VALUES (?,?)",
                                       (receipt_ref, encode(document)))
                return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
                    invocation.arguments_hash, invocation.descriptor.binding, "failed", "not_committed",
                    receipt_ref=receipt_ref)
        request = invocation_request(invocation)
        if action in IMAGE_ACTIONS and self.runtime is not None:
            from companion_v2.conversation.media.output_settings import effective_settings, apply_prompts
            preferences = effective_settings(self.runtime, invocation.context)
            prompts = apply_prompts(request["arguments"], action, preferences)
            request["image_prompt"] = {"positive":prompts["prompt"], "negative":prompts["negative_prompt"]}
            nai_prompts = apply_prompts(request["arguments"], action,
                                       {**preferences, "style": "custom", "style_prompt": ""})
            request["nai_prompt"] = {"positive": nai_prompts["prompt"],
                                    "negative": request["arguments"].get("negative_prompt", "")}
        if invocation.descriptor.tool.action == TOGETHER_CREATE_ACTION:
            if self.persona_guard is None:
                raise TaskError("native_persona_guard_missing", "room creation requires a verified host persona")
            request["host_persona"] = await self.persona_guard(
                invocation.context.scope, invocation.context.role_binding)
            request["host_execution"] = together_room_execution(
                self.together_execution, entry[1].instance)
            guard()
        if self.content is not None and invocation.descriptor.tool.action in CONTENT_ACTIONS:
            self.content.sources.synchronize()
        # Providers receive a copy; mutation cannot alter receipt validation.
        # The synchronous closure expires with this single awaited provider call.
        async def call(method, document, *, image_references=None):
            if not inspect.iscoroutinefunction(method):
                raise TaskError("native_port_invalid", "native I/O must be cooperative asynchronous code")
            active, guarded = True, False

            def before_dispatch():
                nonlocal guarded
                if not active:
                    raise TaskError("native_guard_expired", "the original native dispatch boundary has closed")
                guard()
                if screen_check is not None:
                    self.screen.recheck(invocation, *screen_check)
                if camera_check is not None:
                    self.camera.recheck(invocation, camera_check)
                if image_references is not None:
                    image_references.recheck()
                guarded = True

            guard()
            try:
                binding = (self.content.bind(invocation, document, before_dispatch)
                    if self.content is not None and invocation.descriptor.tool.action in CONTENT_ACTIONS else nullcontext())
                with binding:
                    kwargs = {"before_dispatch": before_dispatch}
                    if image_references is not None:
                        parameters = inspect.signature(method).parameters
                        if {"host_references", "fetch_reference"}.issubset(parameters):
                            async def fetch_reference(handle, content_hash, *, role):
                                before_dispatch()
                                return image_references.fetch(handle, content_hash, role=role)
                            kwargs.update(host_references=image_references.offers, fetch_reference=fetch_reference)
                        elif invocation.descriptor.tool.action == "image.mask_edit":
                            raise TaskError("native_reference_reader_unavailable", "the image provider does not accept host image references")
                    result = await method(json_document(document), **kwargs)
                return result, guarded
            finally:
                active = False

        raw, guarded = await call(port.execute, request, image_references=reference_lease)
        # A receipt already returned by the provider is a fact about the original
        # operation. Later loss of access revokes materials, not that fact.
        native = parse_result(raw, request=request, side_effect=invocation.descriptor.side_effect)
        if native.state != "succeeded":
            return self._source_response(invocation, native)
        try:
            if not guarded:
                raise TaskError("native_guard_unused", "the native provider omitted its dispatch check")
            await self._role(host_tool_id, invocation)
            if reference_lease is not None:
                reference_lease.recheck()
            artifacts = []
            for metadata in native.artifacts:
                artifact_request = {**request, "operation_id": metadata.operation_id,
                                    "artifact_id": metadata.artifact_id, "content_hash": metadata.content_hash}
                content, guarded = await call(getattr(port, "read_artifact", None), artifact_request)
                if not guarded:
                    raise TaskError("native_guard_unused", "the native provider omitted its dispatch check")
                await self._role(host_tool_id, invocation)
                try:
                    artifacts.append(metadata.with_content(content))
                except ValueError as error:
                    raise TaskError("native_artifact_changed", "native bytes differ from the immutable artifact receipt") from error
            adopted = replace(native, artifacts=tuple(artifacts))
            guard()
            handler = self.result_handlers.get(invocation.descriptor.tool.action)
            if self._default_handlers and action in IMAGE_ACTIONS:
                handler = lambda original, received: image_result_handler(
                    original, received, observed_at=self.now(),
                    input_references=() if reference_lease is None else reference_lease.used_references)
            if self._default_handlers and self.content is not None and invocation.descriptor.tool.action in CONTENT_ACTIONS:
                handler = self.content.adopt
            if self._default_handlers and screen_check is not None:
                handler = lambda original, received: self.screen.adopt(original, received, *screen_check)
            # Keep Screen publication and the final host fence in one commit.
            # A failed fence must not leave a governed capture behind.
            adoption = self.runtime.transaction() if screen_check is not None else nullcontext()
            with adoption:
                if handler is None:
                    if artifacts:
                        raise TaskError("native_artifact_unadopted", "native artifacts require an explicit domain adopter")
                    result = self._source_response(invocation, adopted)
                else:
                    result = handler(invocation, adopted)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TaskError("native_handler_invalid", "native adoption must be synchronous")
                self._validate_response(invocation, adopted, result)
                guard()
                if camera_check is not None:
                    self.camera.recheck(invocation, camera_check)
            return result
        except (Exception, asyncio.CancelledError) as error:
            code = (error.code if isinstance(error, TaskError) else "native_adoption_cancelled"
                    if isinstance(error, asyncio.CancelledError) else "native_adoption_unavailable")
            return self._source_response(invocation, native, unavailable_code=code)

    @staticmethod
    def _validate_response(invocation, native, result):
        if not isinstance(result, ExternalToolResponse) or (
                result.invocation_id, result.origin_key, result.arguments_hash, result.binding,
                result.state, result.effect, result.operation_id, result.receipt_ref) != (
                invocation.invocation_id, invocation.context.key, invocation.arguments_hash, invocation.descriptor.binding,
                native.state, native.effect, native.operation_id, native.receipt_ref):
            raise TaskError("native_handler_invalid", "adoption must preserve the original native receipt")
        if native.state == "succeeded":
            source = result.source
            if (source.reference.scope != invocation.context.scope or source.audience != invocation.context.audience
                    or source.reference.owner != invocation.descriptor.source_owner
                    or source.content_hash != digest(source.payload)
                    or json.loads(source.payload).get("native") != native.metadata(invocation.descriptor.binding)):
                raise TaskError("native_handler_invalid", "adoption must preserve native metadata and exact audience")

    def _source_response(self, invocation, native, *, unavailable_code=None):
        source = None
        if native.state == "succeeded":
            metadata = {"native": native.metadata(invocation.descriptor.binding)}
            if unavailable_code:
                metadata["adoption"] = {"state": "unavailable", "code": unavailable_code}
            payload = encode(metadata)
            reference = SourceRef("native:" + invocation.invocation_id, invocation.descriptor.source_owner,
                invocation.context.scope, VersionRef(invocation.invocation_id, 1), self.now())
            source = SourceRecord(reference, invocation.context.audience, digest(payload), payload,
                                  GovernanceState.REVOKED if unavailable_code else GovernanceState.ACTIVE)
        return ExternalToolResponse(invocation.invocation_id, invocation.context.key, invocation.arguments_hash,
            invocation.descriptor.binding, native.state, native.effect, source=source,
            operation_id=native.operation_id, receipt_ref=native.receipt_ref)

    def executor(self, runtime):
        return map_tools(runtime, self) if self.declarations() else None

    def close(self):
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
        if self.content is not None:
            self.content.close()
        for lease in self._bundled_extensions:
            try:
                lease.close()
            except Exception:
                pass
        self._closed = True
        self.application = self.runtime = self.role_guard = self.persona_guard = self.screen = None


__all__ = ["NativeExtensionRegistry"]
