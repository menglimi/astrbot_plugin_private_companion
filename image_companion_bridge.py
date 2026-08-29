# -*- coding: utf-8 -*-
"""Required bridge to the standalone image companion plugin.

The public request shape deliberately mirrors the former private generator so
existing commands, tool calls and proactive flows keep their exact delivery
contract while image execution moves out of the core companion package.
"""
from __future__ import annotations

import asyncio
import os
import re
import stat
from typing import Any

from astrbot.api import logger

from .helpers import _single_line
from .external_bridge_resolver import (
    invalidate_external_bridge_cache,
    resolve_external_bridge,
)


_IMAGE_PLUGIN_ID = "astrbot_plugin_image_companion"
_IMAGE_OWNER_ID = "astrbot_plugin_private_companion"
_IMAGE_API_FAMILY = "image.generation"
_IMAGE_API_VERSION = "image.generation-api.v1"
_IMAGE_TASK_VERSION = "image.task.v1"
_IMAGE_ACTIVE_EXECUTION_CAPABILITY = "image.execute-task.active"
_IMAGE_RESULT_VERSION = "image.result.v1"
_IMAGE_REFERENCE_IMPORT_VERSION = "image.reference-import.v1"
_IMAGE_REFERENCE_IMPORT_RESULT_VERSION = "image.reference-import-result.v1"
_IMAGE_REFERENCE_IMPORT_TTL_SECONDS = 90
_IMAGE_REFERENCE_MAX_ASSETS = 4
_IMAGE_REFERENCE_MAX_BYTES = 20 * 1024 * 1024
_IMAGE_OUTPUT_MAX_BYTES = 50 * 1024 * 1024
_IMAGE_DESCRIPTOR_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "supported_task_versions",
        "capabilities",
        "lifecycle_state",
        "degraded_reasons",
    }
)
_IMAGE_VERSION_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "task_version",
        "supported_task_versions",
    }
)
_IMAGE_REQUIRED_CAPABILITIES = frozenset(
    {
        "image.build-task",
        "image.validate-task",
        "image.import-references",
        "image.release-reference-import",
        "image.execute-task",
    }
)
_IMAGE_IMPORT_RESULT_FIELDS = frozenset(
    {
        "result_version",
        "status",
        "instance_generation",
        "lease_id",
        "asset_ids",
        "ttl_seconds",
        "error",
    }
)
_IMAGE_RESULT_FIELDS = frozenset(
    {
        "result_version",
        "task_version",
        "request_id",
        "status",
        "backend",
        "backend_task_id",
        "output",
        "error",
        "degraded_capabilities",
    }
)
_IMAGE_OUTPUT_FIELDS = frozenset(
    {"asset_id", "kind", "media_type", "local_path", "sha256", "size_bytes"}
)
_IMAGE_API_UNSET = object()
_IMAGE_LEASE_RE = re.compile(r"^reflease_[0-9a-f]{48}$")
_IMAGE_REFERENCE_ASSET_RE = re.compile(r"^ref_[0-9a-f]{48}$")
_IMAGE_OUTPUT_ASSET_RE = re.compile(r"^image_[0-9a-f]{32}$")
_IMAGE_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_IMAGE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
_IMAGE_PROMPT_SECTION_FIELDS = (
    "name",
    "source",
    "positive",
    "negative",
    "protected",
    "sanitize_conflicts",
)


class _ImageCurrentContractError(RuntimeError):
    """Body-free current-contract refusal used only inside this bridge."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ImageCompanionBridgeMixin:
    def _image_companion_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="image_companion",
            module_names=(
                "data.plugins.astrbot_plugin_image_companion.main",
                "astrbot_plugin_image_companion.main",
            ),
            getter_name="get_image_companion_api",
            star_name="astrbot_plugin_image_companion",
        )

    def _image_companion_api_fresh(self) -> Any | None:
        invalidate_external_bridge_cache(self, "image_companion")
        return self._image_companion_api()

    @staticmethod
    def _image_contract_sequence(value: Any) -> tuple[str, ...] | None:
        if type(value) is not list or any(type(item) is not str for item in value):
            return None
        if len(value) != len(set(value)):
            return None
        return tuple(value)

    def _image_companion_contract(
        self,
        *,
        api: Any = _IMAGE_API_UNSET,
        expected_generation: int = 0,
        _refreshed: bool = False,
    ) -> tuple[str, Any | None, int, str]:
        """Negotiate the formal Image API and its one-release rollout mode."""

        pinned_api = api is not _IMAGE_API_UNSET
        candidate = api if pinned_api else self._image_companion_api()
        if candidate is None:
            return "missing", None, 0, "image_companion_unavailable"
        missing = object()
        try:
            descriptor_getter = getattr(candidate, "capabilities", missing)
        except Exception:
            return "incompatible", candidate, 0, "descriptor_method_unreadable"
        if not callable(descriptor_getter):
            if not pinned_api and not _refreshed:
                replacement = self._image_companion_api_fresh()
                if replacement is not candidate:
                    return self._image_companion_contract(_refreshed=True)
            return "incompatible", candidate, 0, "descriptor_method_missing"

        try:
            descriptor = descriptor_getter()
        except Exception:
            return "incompatible", candidate, 0, "descriptor_query_failed"
        if type(descriptor) is not dict or set(descriptor) != _IMAGE_DESCRIPTOR_FIELDS:
            return "incompatible", candidate, 0, "descriptor_malformed"
        generation = descriptor.get("instance_generation")
        versions = self._image_contract_sequence(
            descriptor.get("supported_task_versions")
        )
        capabilities = self._image_contract_sequence(descriptor.get("capabilities"))
        degraded = self._image_contract_sequence(descriptor.get("degraded_reasons"))
        if (
            descriptor.get("plugin_id") != _IMAGE_PLUGIN_ID
            or descriptor.get("api_family") != _IMAGE_API_FAMILY
            or descriptor.get("api_version") != _IMAGE_API_VERSION
            or type(generation) is not int
            or generation <= 0
            or (expected_generation and generation != expected_generation)
            or versions is None
            or _IMAGE_TASK_VERSION not in versions
            or capabilities is None
            or not _IMAGE_REQUIRED_CAPABILITIES.issubset(capabilities)
            or degraded is None
        ):
            return "incompatible", candidate, 0, "descriptor_incompatible"
        if (
            not pinned_api
            and not _refreshed
            and descriptor.get("lifecycle_state") == "closed"
        ):
            replacement = self._image_companion_api_fresh()
            if replacement is not candidate:
                return self._image_companion_contract(_refreshed=True)
        if descriptor.get("lifecycle_state") != "ready" or degraded:
            return "incompatible", candidate, generation, "service_not_ready"

        try:
            versions_getter = getattr(candidate, "versions", None)
            builder = getattr(candidate, "build_task", None)
            validator = getattr(candidate, "validate_task", None)
            importer = getattr(candidate, "import_references", None)
            releaser = getattr(candidate, "release_reference_import", None)
            executor = getattr(candidate, "execute_task", None)
        except Exception:
            return "incompatible", candidate, generation, "required_method_unreadable"
        if not all(
            callable(item)
            for item in (
                versions_getter,
                builder,
                validator,
                importer,
                releaser,
                executor,
            )
        ):
            return "incompatible", candidate, generation, "required_method_missing"
        try:
            version_info = versions_getter()
        except Exception:
            return "incompatible", candidate, generation, "version_query_failed"
        if type(version_info) is not dict or set(version_info) != _IMAGE_VERSION_FIELDS:
            return "incompatible", candidate, generation, "version_descriptor_malformed"
        version_supported = self._image_contract_sequence(
            version_info.get("supported_task_versions")
        )
        if (
            version_info.get("plugin_id") != _IMAGE_PLUGIN_ID
            or version_info.get("instance_generation") != generation
            or version_info.get("api_family") != _IMAGE_API_FAMILY
            or version_info.get("api_version") != _IMAGE_API_VERSION
            or version_info.get("task_version") != _IMAGE_TASK_VERSION
            or version_supported != versions
        ):
            return "incompatible", candidate, generation, "version_descriptor_incompatible"
        if _IMAGE_ACTIVE_EXECUTION_CAPABILITY not in capabilities:
            try:
                compatibility_generator = getattr(
                    candidate,
                    "generate_for_companion",
                    None,
                )
            except Exception:
                compatibility_generator = None
            if not callable(compatibility_generator):
                return (
                    "incompatible",
                    candidate,
                    generation,
                    "active_execution_unavailable",
                )
            return "current_compat", candidate, generation, "active_execution_inactive"
        return "current", candidate, generation, ""

    def _image_require_current_api(self, api: Any, generation: int) -> None:
        candidate = self._image_companion_api_fresh()
        mode, current, current_generation, reason = self._image_companion_contract(
            api=candidate,
            expected_generation=generation,
        )
        if (
            mode != "current"
            or current is not api
            or current_generation != generation
        ):
            raise _ImageCurrentContractError(reason or "image_instance_changed")

    def _image_require_compat_api(self, api: Any, generation: int) -> None:
        candidate = self._image_companion_api_fresh()
        mode, current, current_generation, reason = self._image_companion_contract(
            api=candidate,
            expected_generation=generation,
        )
        if (
            mode != "current_compat"
            or current is not api
            or current_generation != generation
        ):
            raise _ImageCurrentContractError(reason or "image_instance_changed")

    @staticmethod
    def _image_reference_paths(request: dict[str, Any]) -> tuple[str, ...]:
        values: list[Any] = [request.get("reference_image_path", "")]
        extra = request.get("reference_image_paths", ())
        if type(extra) is str:
            values.append(extra)
        elif type(extra) in (list, tuple):
            values.extend(extra)
        elif extra not in (None, ()):
            raise _ImageCurrentContractError("reference_path_invalid")
        result: list[str] = []
        for value in values:
            if value in (None, ""):
                continue
            if (
                type(value) is not str
                or len(value) > 4096
                or "\x00" in value
                or not os.path.isabs(value)
            ):
                raise _ImageCurrentContractError("reference_path_invalid")
            if value not in result:
                result.append(value)
        if len(result) > _IMAGE_REFERENCE_MAX_ASSETS:
            raise _ImageCurrentContractError("reference_import_too_many")
        return tuple(result)

    @staticmethod
    def _image_read_reference_batch_sync(paths: tuple[str, ...]) -> tuple[bytes, ...]:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if paths and (nofollow is None or nonblock is None):
            raise _ImageCurrentContractError("reference_import_store_unsafe")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if nofollow is not None:
            flags |= nofollow
        if nonblock is not None:
            flags |= nonblock
        result: list[bytes] = []
        for path in paths:
            descriptor = -1
            try:
                descriptor = os.open(path, flags)
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise _ImageCurrentContractError("reference_asset_not_regular")
                if before.st_size <= 0 or before.st_size > _IMAGE_REFERENCE_MAX_BYTES:
                    raise _ImageCurrentContractError("reference_import_too_large")
                chunks: list[bytes] = []
                total = 0
                while total <= _IMAGE_REFERENCE_MAX_BYTES:
                    chunk = os.read(
                        descriptor,
                        min(1024 * 1024, _IMAGE_REFERENCE_MAX_BYTES + 1 - total),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                after = os.fstat(descriptor)
                if (
                    total != before.st_size
                    or total > _IMAGE_REFERENCE_MAX_BYTES
                    or before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                ):
                    raise _ImageCurrentContractError("reference_asset_changed")
                content = b"".join(chunks)
                if not (
                    content.startswith(b"\x89PNG\r\n\x1a\n")
                    or content.startswith(b"\xff\xd8\xff")
                    or (
                        len(content) >= 12
                        and content.startswith(b"RIFF")
                        and content[8:12] == b"WEBP"
                    )
                ):
                    raise _ImageCurrentContractError("reference_import_magic_invalid")
                result.append(content)
            except _ImageCurrentContractError:
                raise
            except (OSError, TypeError, ValueError):
                raise _ImageCurrentContractError("reference_asset_unavailable") from None
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
        return tuple(result)

    @staticmethod
    async def _image_read_reference_batch(paths: tuple[str, ...]) -> tuple[bytes, ...]:
        if not paths:
            return ()
        worker = asyncio.create_task(
            asyncio.to_thread(
                ImageCompanionBridgeMixin._image_read_reference_batch_sync,
                paths,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                cancellation = exc
            except BaseException:
                break
        try:
            result = worker.result()
        except BaseException:
            if cancellation is not None:
                raise cancellation
            raise
        if cancellation is not None:
            raise cancellation
        return result

    @staticmethod
    def _image_request_binding(request: dict[str, Any]) -> tuple[str, str, str, str]:
        requester = request.get("requester_user_id", "")
        private = request.get("requester_is_private")
        if type(private) is bool:
            scope = "private" if private else "group"
            privacy = scope
        elif type(requester) is str and requester.strip():
            scope = "proactive"
            privacy = "private"
        else:
            scope = "system"
            privacy = "private"
        session = request.get("session_key", "")
        if type(session) is str and session:
            session_id = session
        else:
            session_id = "companion:image"
        return requester, scope, privacy, session_id

    @staticmethod
    def _image_prompt_sections(value: Any) -> Any:
        if value is None:
            return None
        if type(value) not in (list, tuple):
            return value
        result: list[Any] = []
        for item in value:
            if type(item) is dict:
                result.append(dict(item))
                continue
            try:
                result.append(
                    {
                        field: getattr(item, field)
                        for field in _IMAGE_PROMPT_SECTION_FIELDS
                    }
                )
            except Exception:
                result.append(item)
        return result

    @staticmethod
    def _image_reference_roles(
        request: dict[str, Any],
        count: int,
    ) -> list[list[str]]:
        supplied = request.get("reference_asset_roles")
        if type(supplied) in (list, tuple):
            return [
                list(item) if type(item) in (list, tuple) else item
                for item in supplied
            ]
        workflow = str(request.get("workflow_kind") or "").strip().lower()
        if workflow in {"edit", "改图", "修图", "重绘", "p图"}:
            return [["source"] if index == 0 else [] for index in range(count)]
        if workflow in {"selfie", "portrait", "自拍", "人像"}:
            return [["identity"] for _index in range(count)]
        return [["style"] for _index in range(count)]

    def _image_task_builder_input(
        self,
        request: dict[str, Any],
        asset_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        requester, scope, privacy, session_id = self._image_request_binding(request)
        value: dict[str, Any] = {
            "owner_id": _IMAGE_OWNER_ID,
            "workflow_kind": request.get("workflow_kind", ""),
            "prompt_text": request.get("prompt_text", ""),
            "request_text": request.get("request_text", ""),
            "requester_user_id": requester,
            "scope": scope,
            "privacy": privacy,
            "session_key": session_id,
            "continuity_key": request.get("continuity_key", ""),
            "allow_daily_outfit_reference": request.get(
                "allow_daily_outfit_reference",
                True,
            ),
            "requested_scene_preset": request.get("requested_scene_preset")
            or request.get("suggested_scene_preset", ""),
            "workflow_default_scene_preset": request.get(
                "workflow_default_scene_preset",
                "",
            ),
            "reference_asset_ids": list(asset_ids),
            "reference_asset_roles": self._image_reference_roles(
                request,
                len(asset_ids),
            ),
            "limits": {"image_size": request.get("image_size", "")},
        }
        if "prompt_format" in request:
            value["prompt_format"] = request.get("prompt_format")
        sections = self._image_prompt_sections(request.get("prompt_sections"))
        if sections is not None:
            value["prompt_sections"] = sections
        for field in ("reference_intent", "scene", "character"):
            if field in request:
                value[field] = request.get(field)
        return value

    @staticmethod
    def _image_import_receipt(
        value: Any,
        *,
        generation: int,
        expected_assets: int,
    ) -> tuple[str, tuple[str, ...]]:
        if type(value) is not dict or set(value) != _IMAGE_IMPORT_RESULT_FIELDS:
            raise _ImageCurrentContractError("reference_import_result_malformed")
        if (
            value.get("result_version") != _IMAGE_REFERENCE_IMPORT_RESULT_VERSION
            or value.get("instance_generation") != generation
        ):
            raise _ImageCurrentContractError("reference_import_result_incompatible")
        if value.get("status") == "failed":
            error = value.get("error")
            code = error.get("code") if type(error) is dict else ""
            if type(code) is not str or _IMAGE_ERROR_CODE_RE.fullmatch(code) is None:
                code = "reference_import_failed"
            raise _ImageCurrentContractError(code)
        assets = value.get("asset_ids")
        lease_id = value.get("lease_id")
        if (
            value.get("status") != "succeeded"
            or type(lease_id) is not str
            or _IMAGE_LEASE_RE.fullmatch(lease_id) is None
            or type(assets) is not list
            or len(assets) != expected_assets
            or len(assets) != len(set(assets))
            or any(
                type(asset) is not str
                or _IMAGE_REFERENCE_ASSET_RE.fullmatch(asset) is None
                for asset in assets
            )
            or value.get("ttl_seconds") != _IMAGE_REFERENCE_IMPORT_TTL_SECONDS
            or value.get("error") is not None
        ):
            raise _ImageCurrentContractError("reference_import_result_malformed")
        return lease_id, tuple(assets)

    @staticmethod
    def _image_validate_task_receipt(value: Any) -> None:
        if (
            type(value) is not dict
            or set(value) != {"valid", "task_version", "operation", "workflow_kind"}
            or value.get("valid") is not True
            or value.get("task_version") != _IMAGE_TASK_VERSION
            or value.get("operation") != "generate"
            or type(value.get("workflow_kind")) is not str
        ):
            raise _ImageCurrentContractError("image_task_validation_malformed")

    def _image_result_tuple(
        self,
        value: Any,
        *,
        reference_roles: list[list[str]],
    ) -> tuple[str, str, str]:
        if type(value) is not dict or set(value) != _IMAGE_RESULT_FIELDS:
            raise _ImageCurrentContractError("image_result_malformed")
        request_id = value.get("request_id")
        degraded = value.get("degraded_capabilities")
        if (
            value.get("result_version") != _IMAGE_RESULT_VERSION
            or value.get("task_version") != _IMAGE_TASK_VERSION
            or type(request_id) is not str
            or _IMAGE_REQUEST_ID_RE.fullmatch(request_id) is None
            or type(value.get("backend")) is not str
            or value.get("backend") not in {"", "comfyui", "external"}
            or type(value.get("backend_task_id")) is not str
            or len(value.get("backend_task_id")) > 160
            or type(degraded) is not list
            or len(degraded) != len(set(degraded))
            or any(type(item) is not str or len(item) > 120 for item in degraded)
        ):
            raise _ImageCurrentContractError("image_result_malformed")
        if value.get("status") == "failed":
            error = value.get("error")
            if value.get("output") is not None or type(error) is not dict or set(error) != {
                "code",
                "stage",
            }:
                raise _ImageCurrentContractError("image_result_malformed")
            code = error.get("code")
            stage = error.get("stage")
            if (
                type(code) is not str
                or _IMAGE_ERROR_CODE_RE.fullmatch(code) is None
                or type(stage) is not str
                or _IMAGE_ERROR_CODE_RE.fullmatch(stage) is None
            ):
                raise _ImageCurrentContractError("image_result_malformed")
            return (
                "独立生图服务",
                "",
                f"“我会画给你看”任务失败（{code}）。",
            )
        output = value.get("output")
        if (
            value.get("status") != "succeeded"
            or value.get("error") is not None
            or type(output) is not dict
            or set(output) != _IMAGE_OUTPUT_FIELDS
        ):
            raise _ImageCurrentContractError("image_result_malformed")
        path = output.get("local_path")
        size = output.get("size_bytes")
        if (
            type(output.get("asset_id")) is not str
            or _IMAGE_OUTPUT_ASSET_RE.fullmatch(output.get("asset_id")) is None
            or output.get("kind") != "image"
            or output.get("media_type") not in {"image/png", "image/jpeg", "image/webp"}
            or type(path) is not str
            or not path
            or len(path) > 4096
            or "\x00" in path
            or not os.path.isabs(path)
            or type(output.get("sha256")) is not str
            or _IMAGE_SHA256_RE.fullmatch(output.get("sha256")) is None
            or type(size) is not int
            or size <= 0
            or size > _IMAGE_OUTPUT_MAX_BYTES
        ):
            raise _ImageCurrentContractError("image_result_malformed")
        flattened_roles = list(
            dict.fromkeys(role for roles in reference_roles for role in roles)
        )
        self._image_companion_generation_metadata = {
            "trace": request_id,
            "managed_asset_id": output["asset_id"],
            "reference_used": bool(reference_roles),
            "reference_roles": flattened_roles,
            "output_sha256": output["sha256"],
        }
        return (
            value.get("backend") or "独立生图服务",
            path,
            "已通过 ImageTask v1 生成受管图片。",
        )

    async def _image_companion_generate_current(
        self,
        api: Any,
        generation: int,
        request: dict[str, Any],
    ) -> tuple[str, str, str]:
        paths = self._image_reference_paths(request)
        contents = await self._image_read_reference_batch(paths)
        self._image_require_current_api(api, generation)
        requester, scope, privacy, session_id = self._image_request_binding(request)
        lease_id = ""
        asset_ids: tuple[str, ...] = ()
        reference_roles: list[list[str]] = []
        try:
            if contents:
                receipt = await api.import_references(
                    {
                        "version": _IMAGE_REFERENCE_IMPORT_VERSION,
                        "owner_id": _IMAGE_OWNER_ID,
                        "requester_id": requester,
                        "scope": scope,
                        "privacy": privacy,
                        "session_id": session_id,
                        "assets": [{"content": content} for content in contents],
                    }
                )
                if type(receipt) is dict:
                    candidate_lease = receipt.get("lease_id")
                    if (
                        type(candidate_lease) is str
                        and _IMAGE_LEASE_RE.fullmatch(candidate_lease) is not None
                    ):
                        lease_id = candidate_lease
                parsed_lease, asset_ids = self._image_import_receipt(
                    receipt,
                    generation=generation,
                    expected_assets=len(contents),
                )
                lease_id = parsed_lease
                self._image_require_current_api(api, generation)

            builder_input = self._image_task_builder_input(request, asset_ids)
            reference_roles = builder_input["reference_asset_roles"]
            task = api.build_task(builder_input)
            if type(task) is not dict:
                raise _ImageCurrentContractError("image_task_build_malformed")
            validation = api.validate_task(task)
            self._image_validate_task_receipt(validation)
            self._image_require_current_api(api, generation)
            result = await api.execute_task(task)
            self._image_require_current_api(api, generation)
            return self._image_result_tuple(
                result,
                reference_roles=reference_roles,
            )
        finally:
            if lease_id:
                try:
                    api.release_reference_import(lease_id)
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanion] Image 临时参考租约撤权失败: error_type=%s",
                        type(exc).__name__,
                    )

    def _image_companion_required(self) -> bool:
        """Return whether this object is the production companion host."""
        return self.__class__.__name__ == "PrivateCompanionPlugin" or any(
            base.__name__ == "PrivateCompanionPlugin"
            for base in getattr(self.__class__, "__mro__", ())
        )

    def _image_companion_status(self) -> dict[str, Any]:
        """Return the split service status without importing its runtime."""
        mode, api, _generation, reason = self._image_companion_contract()
        if api is None:
            return {
                "installed": False,
                "enabled": False,
                "available": False,
                "reason": "image_companion_unavailable",
                "backup_external_note": "image_companion_unavailable",
                "backends": {},
            }
        if mode in {"current", "current_compat"}:
            try:
                getter = getattr(api, "status", None)
                if mode == "current":
                    self._image_require_current_api(api, _generation)
                else:
                    self._image_require_compat_api(api, _generation)
                status = getter() if callable(getter) else {}
                if mode == "current":
                    self._image_require_current_api(api, _generation)
                else:
                    self._image_require_compat_api(api, _generation)
            except _ImageCurrentContractError as exc:
                return {
                    "installed": True,
                    "enabled": False,
                    "available": False,
                    "reason": exc.code,
                    "backup_external_note": exc.code,
                    "backends": {},
                }
            except Exception:
                return {
                    "installed": True,
                    "enabled": False,
                    "available": False,
                    "reason": "image_status_query_failed",
                    "backup_external_note": "image_status_query_failed",
                    "backends": {},
                }
            if type(status) is not dict:
                return {
                    "installed": True,
                    "enabled": False,
                    "available": False,
                    "reason": "image_status_malformed",
                    "backup_external_note": "image_status_malformed",
                    "backends": {},
                }
            result = dict(status)
            result.setdefault("installed", True)
            result.setdefault("enabled", True)
            result.setdefault("available", bool(result.get("enabled")))
            result.setdefault("reason", "")
            result.setdefault("backends", {})
            return result
        return {
            "installed": True,
            "enabled": False,
            "available": False,
            "reason": reason or "image_contract_incompatible",
            "backup_external_note": reason or "image_contract_incompatible",
            "backends": {},
        }

    def _image_companion_available(self) -> bool:
        return bool(self._image_companion_status().get("available"))

    def _image_companion_backend_available(self, backend: str) -> bool:
        status = self._image_companion_status()
        backends = status.get("backends")
        return bool(backends.get(backend)) if isinstance(backends, dict) else False

    def _image_companion_load_state(self, *, force_refresh: bool = False) -> dict[str, Any]:
        mode, api, _generation, reason = self._image_companion_contract()
        if api is None:
            return {"enabled": False, "available": False, "busy": False, "reason": "独立生图插件不可用"}
        if mode in {"current", "current_compat"}:
            status = self._image_companion_status()
            return {
                "enabled": bool(status.get("enabled")),
                "available": bool(status.get("available")),
                "busy": False,
                "reason": _single_line(status.get("reason"), 160),
            }
        return {
            "enabled": False,
            "available": False,
            "busy": False,
            "reason": reason or "image_contract_incompatible",
        }

    async def _image_companion_maintenance(self) -> dict[str, Any]:
        mode, api, generation, _reason = self._image_companion_contract()
        if mode == "current":
            # Current Image owns its transient sweeper and state maintenance;
            # Companion must not reintroduce owner injection for housekeeping.
            return {}
        maintainer = (
            getattr(api, "maintenance", None)
            if mode == "current_compat" and api is not None
            else None
        )
        if not callable(maintainer):
            return {}
        try:
            self._image_require_compat_api(api, generation)
            result = await maintainer(self)
            self._image_require_compat_api(api, generation)
        except _ImageCurrentContractError as exc:
            logger.warning(
                "[PrivateCompanion] Image 兼容维护路径已变更: code=%s",
                exc.code,
            )
            return {}
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图后台维护失败: error_type=%s",
                type(exc).__name__,
            )
            return {}
        return dict(result) if isinstance(result, dict) else {}

    async def _image_companion_generate(self, **request: Any) -> tuple[str, str, str]:
        """Delegate every image request to the split plugin.

        The host keeps the historical request/response shape so commands and
        delivery order remain unchanged, but it no longer executes an image
        backend locally.
        """
        api = self._image_companion_api_fresh()
        mode, api, generation, reason = self._image_companion_contract(api=api)
        if mode == "current" and api is not None:
            try:
                return await self._image_companion_generate_current(
                    api,
                    generation,
                    dict(request),
                )
            except asyncio.CancelledError:
                raise
            except _ImageCurrentContractError as exc:
                logger.warning(
                    "[PrivateCompanion] Image current contract 拒绝请求: workflow=%s code=%s",
                    _single_line(request.get("workflow_kind"), 40),
                    exc.code,
                )
                return (
                    "独立生图服务",
                    "",
                    f"“我会画给你看”任务未执行（{exc.code}）。",
                )
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] Image current contract 调用异常: workflow=%s error_type=%s",
                    _single_line(request.get("workflow_kind"), 40),
                    type(exc).__name__,
                )
                return (
                    "独立生图服务",
                    "",
                    "“我会画给你看”暂时不可用，请检查该插件状态和生图排障记录。",
                )
        if mode == "incompatible":
            return (
                "独立生图服务",
                "",
                f"“我会画给你看”当前契约不兼容（{reason}），已拒绝降级执行。",
            )
        generator = (
            getattr(api, "generate_for_companion", None)
            if mode == "current_compat" and api is not None
            else None
        )
        if not callable(generator):
            return (
                "独立生图服务",
                "",
                "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
            )
        try:
            self._image_require_compat_api(api, generation)
            response = await generator(self, dict(request))
            self._image_require_compat_api(api, generation)
        except asyncio.CancelledError:
            raise
        except _ImageCurrentContractError as exc:
            logger.warning(
                "[PrivateCompanion] Image 兼容生成路径已变更: workflow=%s code=%s",
                _single_line(request.get("workflow_kind"), 40),
                exc.code,
            )
            return (
                "独立生图服务",
                "",
                f"“我会画给你看”当前契约已变更（{exc.code}），已拒绝接受旧代结果。",
            )
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图插件调用异常: workflow=%s error_type=%s",
                _single_line(request.get("workflow_kind"), 40),
                type(exc).__name__,
            )
            return (
                "独立生图服务",
                "",
                "“我会画给你看”暂时不可用，请检查该插件状态和生图排障记录。",
            )
        if not isinstance(response, dict) or response.get("handled") is not True:
            return (
                "独立生图服务",
                "",
                "“我会画给你看”当前未接管请求，请确认插件已启用。",
            )
        metadata = response.get("metadata")
        self._image_companion_generation_metadata = (
            dict(metadata) if isinstance(metadata, dict) else {}
        )
        return (
            _single_line(response.get("backend"), 80),
            _single_line(response.get("image_path"), 1000),
            _single_line(response.get("note"), 500),
        )

    def _image_companion_last_metadata(self) -> dict[str, Any]:
        value = getattr(self, "_image_companion_generation_metadata", None)
        return dict(value) if isinstance(value, dict) else {}

    async def _image_companion_test_endpoint(
        self,
        endpoint: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        mode, api, generation, _reason = self._image_companion_contract()
        if mode == "current":
            return {
                "ok": False,
                "message": "当前 Image 正式契约不接收 Companion owner 或原始 endpoint，请使用完整图片生成链路测试。",
            }
        tester = (
            getattr(api, "test_endpoint", None)
            if mode == "current_compat" and api is not None
            else None
        )
        if not callable(tester):
            return {"ok": False, "message": "请安装并启用“我会画给你看”后再测试在线图片 API。"}
        try:
            self._image_require_compat_api(api, generation)
            result = await tester(self, dict(endpoint or {}), str(prompt or ""))
            self._image_require_compat_api(api, generation)
            return result
        except asyncio.CancelledError:
            raise
        except _ImageCurrentContractError:
            return {"ok": False, "message": "Image 兼容测试路径已变更，未接受旧代结果。"}
