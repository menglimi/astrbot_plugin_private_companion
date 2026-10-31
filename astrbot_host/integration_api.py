"""Authenticated product manifest and bundled extension lifecycle routes."""

from __future__ import annotations

import json
from typing import Any

from astrbot.api.web import request

from companion_v2.capabilities import CapabilityManagementService, CapabilityRepository
from companion_v2.configuration.models import fingerprint
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from .capability_role_selection import (
    AUTO_ROLE_EXTENSIONS,
    enable_role,
    lifecycle_role_identity,
)
from .management_identity import dashboard_operator
from .integration_registry import UnifiedCompanionExtensionAPI
from .native_extensions.builtins import MANAGED_EXTENSIONS


PREFIX = "/astrbot_plugin_private_companion"


class IntegrationWebAPI:
    def __init__(self, controller: Any):
        self.controller = controller

    def routes(self):
        return [(
            PREFIX + "/integration/manifest",
            self.manifest,
            ["GET"],
            "Read the integrated Companion V2 product manifest",
        ), (
            PREFIX + "/integration/lifecycle",
            self.lifecycle,
            ["POST"],
            "Assemble, update, pause, resume or uninstall a bundled Companion V2 extension",
        )]

    async def manifest(self) -> dict[str, Any]:
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("integration_request_invalid", "the integration manifest does not accept query overrides")
            controller = self.controller
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                runtime = controller.runtime
                if runtime is not None:
                    operator = dashboard_operator(runtime, username, at=controller.now())
                    require(ObjectAccessPolicy(runtime).feature_access(
                        operator,
                        scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                        action="memory.manage",
                        observed_at=controller.now(),
                    ))
                return {"status": "ok", "message": "", "data": UnifiedCompanionExtensionAPI(controller).manifest()}
        except TaskError as error:
            from .web_api import outcome_response

            return outcome_response(Outcome.failed(error.code, str(error)))

    async def lifecycle(self) -> dict[str, Any]:
        from .web_api import outcome_response

        try:
            if request.query:
                raise ValueError("lifecycle commands do not accept query fields")
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("lifecycle command is too large")

            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("duplicate field")
                    values[key] = value
                return values

            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            expected_fields = {"extension_id", "action", "expected_revision", "operation_key"}
            role_fields = {"role_id", "target_id"}
            if type(data) is not dict or set(data) not in (expected_fields, expected_fields | role_fields):
                raise ValueError("lifecycle command fields are incomplete or unknown")
            extension_id, action = data["extension_id"], data["action"]
            revision, operation_key = data["expected_revision"], data["operation_key"]
            role_id, target_id = data.get("role_id"), data.get("target_id")
            if (role_id is None) != (target_id is None):
                raise ValueError("role_id and target_id must be supplied together")
            if role_id is not None and any(
                    type(value) is not str or not 1 <= len(value) <= 160
                    or any(not (char.isascii() and (char.isalnum() or char in "._:-")) for char in value)
                    for value in (role_id, target_id)):
                raise ValueError("role_id and target_id must be bounded stable identifiers")
            if extension_id not in MANAGED_EXTENSIONS:
                raise ValueError("only V2 bundled extensions can be managed here")
            if action not in ("assemble", "update", "pause", "resume", "uninstall"):
                raise ValueError("unknown bundled extension action")
            if type(revision) is not int or revision < 0:
                raise ValueError("expected_revision must be a non-negative integer")
            if (type(operation_key) is not str or not 1 <= len(operation_key) <= 128
                    or any(not (char.isascii() and (char.isalnum() or char in "._:-")) for char in operation_key)):
                raise ValueError("operation_key must be a bounded stable identifier")
            username = request.username
            if (type(username) is not str or not username.strip() or username != username.strip()
                    or username.startswith("api_key:")):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")

            controller = self.controller
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                runtime = controller.runtime
                registry = getattr(controller, "native_extensions", None)
                if runtime is None or registry is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile before managing its extensions")
                observed_at = controller.now()
                operator = dashboard_operator(runtime, username, at=observed_at)
                management = CapabilityManagementService(runtime)
                scope = management.management_scope(extension_id)
                operation = OperationRef(
                    "capability-ui-" + fingerprint([extension_id, operation_key]),
                    management.OWNER,
                    scope,
                    VersionRef("capability-lifecycle", 1),
                    operation_key,
                )

                if action in ("assemble", "update"):
                    package = registry.current_package(extension_id)
                    if package is None:
                        raise TaskError("integration_extension_unavailable", "the bundled extension package is not ready")
                    policy = ObjectAccessPolicy(runtime)
                    require(policy.feature_access(
                        operator, scope=scope,
                        action="capability.acquire" if action == "assemble" else "capability.update",
                        observed_at=observed_at))
                    require(CapabilityRepository(runtime).register_descriptor(
                        package, registered_at=observed_at))
                    result = management.select_package(
                        operator, extension_id=extension_id,
                        action="acquire" if action == "assemble" else "update", package_id=package.package_id,
                        package_version=package.package_version, artifact_hash=package.artifact_hash,
                        expected_revision=revision, operation=operation, observed_at=observed_at)
                elif action == "uninstall":
                    result = management.uninstall(
                        operator, extension_id=extension_id, expected_revision=revision,
                        operation=operation, observed_at=observed_at)
                else:
                    result = management.change_lifecycle(
                        operator, extension_id=extension_id, action=action,
                        expected_revision=revision, operation=operation, observed_at=observed_at)

                receipt = require(result)
                await registry.reconcile_service(extension_id)
                role_selection = None
                if action == "assemble" and extension_id in AUTO_ROLE_EXTENSIONS:
                    identity = lifecycle_role_identity(controller, role_id=role_id, target_id=target_id)
                    if identity is not None:
                        role, bot_id = identity
                        role_selection = enable_role(runtime, extension_id, role, bot_id, observed_at)
                installation = require(CapabilityRepository(runtime).installation(extension_id=extension_id))
                return outcome_response(Outcome.success({"receipt": receipt, "installation": installation,
                    "role_selection": role_selection, "runtime": registry.status(extension_id)}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed(
                "integration_lifecycle_request_invalid", "invalid UTF-8 extension lifecycle command"))
