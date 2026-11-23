"""Pure transport reuse behind a new owner and independent V2 configuration."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .comfyui_workflows import WorkflowError, ancestors
from .image_native_client import NativeClient, NativeRejected
from .image_preparation_task import PreparationError
from .types import (BackendResult, ImageInvocation, NativeImageError, ReferenceImage, document, fingerprint,
                    validate_masked_inputs)


_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")


class GuardedClient(NativeClient):
    def __init__(self, backend, config, directory, fence):
        # Native plans carry embedded graphs. The transport must not resolve the
        # old workflow-file catalog while connecting or recovering a task.
        super().__init__(backend, {**config, "workflows": []}, directory)
        self.fence = fence
        if self.comfy is not None:
            original_request = self.comfy._request

            async def guarded_request(method, path, **kwargs):
                await self._check(method, path)
                return await original_request(method, path, **kwargs)

            self.comfy._request = guarded_request

    async def _check(self, method, path=""):
        # Read checks and ComfyUI input-image staging (/upload/image) happen
        # before /prompt is submitted; they still enforce live authorization
        # without marking the operation as dispatched to remote generation.
        pre_submit = method == "GET" or (self.backend == "comfyui" and path == "/upload/image")
        check = getattr(self.fence, "read", self.fence) if pre_submit else self.fence
        await check()

    async def request(self, method, path, **kwargs):
        await self._check(method, path)
        return await super().request(method, path, **kwargs)

    async def download_comfy(self, url):
        await self._check("GET", "/view")
        return await super().download_comfy(url)


class NativeTransportBackend:
    """The administrator supplies a V2 endpoint and optional embedded workflows.

    No legacy preparation service, routing, file reference catalog, persona
    settings, or old data directory is consulted.
    """

    def __init__(self, backend: str, config: dict, directory: Path):
        if backend not in {"sd_webui", "comfyui"}:
            raise NativeImageError("native_backend_unconfigured")
        if not isinstance(config, dict):
            raise NativeImageError("native_configuration_invalid")
        self.backend = backend
        self.config = json.loads(document(config))
        for name in ("parameters", "workflows"):
            value = self.config.get(name, {})
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except ValueError as exc:
                raise NativeImageError("native_configuration_invalid") from exc
            if not isinstance(value, dict):
                raise NativeImageError("native_configuration_invalid")
            self.config[name] = value
        parameter_config = self.config["parameters"]
        operation_parameters = {name: parameter_config.get(name, {}) for name in ("generate", "edit", "mask_edit")}
        shared_parameters = {key: value for key, value in parameter_config.items()
                             if key not in operation_parameters}
        if any(not isinstance(value, dict) for value in operation_parameters.values()):
            raise NativeImageError("native_configuration_invalid")
        for operation, specific in operation_parameters.items():
            ImageInvocation("configuration", "configuration", "configuration", "configuration", operation,
                            "configuration", parameters={**shared_parameters, **specific})
        if (not isinstance(self.config.get("api_key", ""), str)
                or type(self.config.get("timeout_seconds", 300)) is not int
                or self.config.get("timeout_seconds", 300) < 1):
            raise NativeImageError("native_configuration_invalid")
        if backend == "sd_webui":
            self.operations = ("generate", "edit", "mask_edit")
        else:
            operations = []
            for operation in ("generate", "edit", "mask_edit"):
                if operation not in self.config["workflows"]:
                    continue
                if operation == "mask_edit":
                    try:
                        self._validate_mask_workflow(self.config["workflows"][operation])
                    except (NativeImageError, AttributeError, KeyError, TypeError):
                        continue
                operations.append(operation)
            self.operations = tuple(operations)
        if not self.operations:
            raise NativeImageError("native_workflow_unconfigured")
        raw = str(self.config.get("base_url", "")).strip().rstrip("/")
        parsed = urlsplit(raw)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise NativeImageError("native_endpoint_invalid")
        self.config["base_url"] = raw
        self.directory = Path(directory)
        self.revision = fingerprint({"backend": backend, "config": self.config})
        self._closed = False

    def _live(self):
        if self._closed:
            raise NativeImageError("image_provider_stopped")

    @staticmethod
    def _validate_mask_workflow(route):
        workflow, mapping = route["workflow"], route["mapping"]
        fields = mapping.get("fields", {})
        masks = [(name, field) for name, field in fields.items() if field.get("kind") == "mask"]
        sources = [(name, field) for name, field in fields.items() if field.get("kind") == "image"]
        if (len(masks) != 1 or len(sources) != 1
                or masks[0][1].get("node_id") not in workflow
                or workflow[masks[0][1]["node_id"]].get("class_type") != "ETN_LoadMaskBase64"
                or masks[0][1].get("input_name") != "mask"):
            raise NativeImageError("native_workflow_mask_binding_invalid")
        output_node = mapping.get("output_node")
        if not isinstance(output_node, str) or output_node not in workflow:
            raise NativeImageError("native_workflow_mask_binding_invalid")
        active = ancestors(workflow, [output_node])
        mask_node = masks[0][1]["node_id"]
        if mask_node not in active:
            raise NativeImageError("native_workflow_mask_unconnected")
        connected = any(
            isinstance(value, list) and value[:2] == [mask_node, 0]
            for node_id in active for value in workflow[node_id].get("inputs", {}).values()
        )
        if not connected:
            raise NativeImageError("native_workflow_mask_unconnected")

    def _plan(self, invocation, references: tuple[ReferenceImage, ...] = ()):
        parameters = {"width": 768, "height": 768, "steps": 24, "cfg_scale": 7,
                      "seed": -1, "batch_size": 1, "denoising_strength": 0.65}
        if tuple(item.reference_id for item in references) != invocation.reference_ids:
            raise NativeImageError("reference_binding_mismatch", unconfirmed=False)
        if invocation.operation == "mask_edit":
            if (len(references) != 2
                    or [item.role for item in references].count("edit_source") != 1
                    or [item.role for item in references].count("edit_mask") != 1):
                raise NativeImageError("native_mask_bindings_invalid", unconfirmed=False)
            source = next(item for item in references if item.role == "edit_source")
            mask = next(item for item in references if item.role == "edit_mask")
            try:
                validate_masked_inputs(source.data, mask.data)
            except NativeImageError as exc:
                raise NativeImageError(exc.code, unconfirmed=False) from None
        if self.backend == "sd_webui":
            configured = self.config.get("parameters", {})
            operation = "edit" if invocation.operation == "mask_edit" else invocation.operation
            shared = {key: value for key, value in configured.items()
                      if key not in {"generate", "edit", "mask_edit"}}
            parameters.update(shared)
            parameters.update(configured.get(operation, {}))
            parameters.update(invocation.parameters)
            if parameters.keys() - {"width", "height", "steps", "cfg_scale", "seed", "batch_size", "sampler_name", "scheduler", "denoising_strength"}:
                raise NativeImageError("unsupported_image_parameter", unconfirmed=False)
            if invocation.operation == "edit" and len(references) != 1:
                raise NativeImageError("native_reference_limit", unconfirmed=False)
            if invocation.operation == "mask_edit":
                if (len(references) != 2
                        or [item.role for item in references].count("edit_source") != 1
                        or [item.role for item in references].count("edit_mask") != 1):
                    raise NativeImageError("native_mask_bindings_invalid", unconfirmed=False)
                source = next(item for item in references if item.role == "edit_source")
                mask = next(item for item in references if item.role == "edit_mask")
                return {"operation": invocation.operation, "parameters": parameters,
                        "reference_bindings": [{"slot": "init_images", "input_id": source.reference_id,
                                                "role": "edit_source"}],
                        "edit_region": {"source_input_id": source.reference_id,
                                        "mask_input_id": mask.reference_id}}
            return {"operation": invocation.operation, "parameters": parameters,
                    "reference_bindings": [{"slot": "init_images", "input_id": item.reference_id,
                                            "role": item.role} for item in references]}
        route = self.config.get("workflows", {}).get(invocation.operation)
        if not isinstance(route, dict) or set(route) != {"workflow", "mapping"}:
            raise NativeImageError("native_workflow_unconfigured", unconfirmed=False)
        fields = route["mapping"].get("fields", {})
        images = sorted((key, item) for key, item in fields.items() if item.get("kind") == "image")
        masks = sorted((key, item) for key, item in fields.items() if item.get("kind") == "mask")
        prompts = [item for item in fields.values() if item.get("kind") == "prompt"]
        valid_prompt_modes = {"replace", "append", "preserve"}
        if (not prompts or "positive_prompt" not in fields
                or fields["positive_prompt"].get("mode", "replace") not in {"replace", "append"}
                or any(item.get("mode", "replace") not in valid_prompt_modes for item in prompts)
                or any(item.get("mode", "replace") != "replace" for _, item in (*images, *masks))):
            raise NativeImageError("native_workflow_bindings_invalid", unconfirmed=False)
        if invocation.operation == "mask_edit":
            try:
                self._validate_mask_workflow(route)
            except NativeImageError as exc:
                raise NativeImageError(exc.code, unconfirmed=False) from None
            if (len(references) != 2 or len(images) != 1 or len(masks) != 1
                    or [item.role for item in references].count("edit_source") != 1
                    or [item.role for item in references].count("edit_mask") != 1):
                raise NativeImageError("native_mask_bindings_invalid", unconfirmed=False)
        elif invocation.operation == "edit" and not references:
            raise NativeImageError("missing_edit_source", unconfirmed=False)
        elif invocation.operation == "edit" and (masks or len(images) != len(references)):
            raise NativeImageError("native_workflow_bindings_invalid", unconfirmed=False)
        elif invocation.operation == "generate" and (masks or images or references):
            raise NativeImageError("native_workflow_bindings_invalid", unconfirmed=False)
        covered = {(item["node_id"], item["input_name"]) for _, item in (*images, *masks)}
        if any(node.get("class_type") in {"LoadImage", "ETN_LoadImageBase64"}
               and (key, "image") not in covered for key, node in route["workflow"].items()):
            raise NativeImageError("native_workflow_unbound_reference", unconfirmed=False)
        if any(node.get("class_type") == "ETN_LoadMaskBase64" and (key, "mask") not in covered
               for key, node in route["workflow"].items()):
            raise NativeImageError("native_workflow_unbound_reference", unconfirmed=False)
        role_by_slot = {}
        for name, field in images:
            declared_role = field.get("role", "generic")
            role_by_slot[name] = "" if declared_role in (None, "", "generic") else str(declared_role)
        role_by_slot.update({name: "edit_mask" for name, _ in masks})
        reference_by_role = {item.role: item for item in references} if invocation.operation == "mask_edit" else None
        if invocation.operation == "mask_edit":
            bindings = []
            for name, _ in (*images, *masks):
                slot_role = role_by_slot[name] or "edit_source"
                if slot_role not in reference_by_role:
                    raise NativeImageError("native_workflow_reference_role_mismatch", unconfirmed=False)
                bindings.append({"slot": name, "input_id": reference_by_role[slot_role].reference_id,
                                 "role": slot_role})
        else:
            for (name, _), ref in zip(images, references):
                slot_role = role_by_slot[name]
                if slot_role and slot_role != ref.role:
                    raise NativeImageError("native_workflow_reference_role_mismatch", unconfirmed=False)
            bindings = [{"slot": name, "input_id": ref.reference_id, "role": ref.role}
                        for (name, _), ref in zip(images, references)]
        plan = {"operation": invocation.operation, **route, "parameters": dict(invocation.parameters),
                "reference_bindings": bindings}
        if invocation.operation == "mask_edit":
            reference_by_role = {item.role: item.reference_id for item in references}
            plan["edit_region"] = {"source_input_id": reference_by_role["edit_source"],
                                   "mask_input_id": reference_by_role["edit_mask"]}
        return plan

    async def execute(self, invocation, references, before_dispatch):
        self._live()
        plan = self._plan(invocation, tuple(references))
        client = GuardedClient(self.backend, self.config, self.directory, before_dispatch)
        prompt = {"positive": invocation.prompt, "negative": invocation.negative_prompt}
        reference_bytes = {item.reference_id: item.data for item in references}
        submitted = False
        try:
            if self.backend == "sd_webui":
                submitted = True
                images, receipt = await client.generate_sd(plan, prompt, reference_bytes)
                return BackendResult(tuple(images), receipt=receipt)
            workflow = await client.prepare_comfy(plan, prompt, reference_bytes, lambda: None, lambda _: None)
            output_node = plan["mapping"]["output_node"]
            submitted = True
            task_id = await client.submit_comfy(workflow, output_node)
            locator = {"backend": self.backend, "revision": self.revision, "task_id": task_id, "output_node": output_node}
            return BackendResult(locator=locator, receipt={"backend": self.backend, "task_id": task_id, "submission_confirmed": True})
        except NativeRejected:
            code = "comfyui_rejected" if self.backend == "comfyui" else "sd_webui_rejected"
            raise NativeImageError(code, unconfirmed=False) from None
        except WorkflowError:
            raise NativeImageError("native_workflow_mapping_invalid", unconfirmed=False) from None
        except PreparationError as exc:
            raw_code = getattr(exc, "code", "")
            code = raw_code if isinstance(raw_code, str) and _ERROR_CODE_RE.fullmatch(raw_code) else "backend_result_unconfirmed"
            raise NativeImageError(code, unconfirmed=submitted) from None
        finally:
            await client.close()

    async def recover(self, locator, authorize):
        self._live()
        if self.backend != "comfyui" or locator.get("backend") != self.backend or locator.get("revision") != self.revision:
            raise NativeImageError("native_backend_changed")
        client = GuardedClient(self.backend, self.config, self.directory, authorize)
        try:
            result = await client.poll_comfy(locator["task_id"], locator["output_node"])
            if result["status"] != "completed":
                return BackendResult(locator=locator, receipt={"backend": self.backend, "task_id": locator["task_id"], "status": result["status"]})
            images = []
            for output in result["outputs"]:
                images.append(await client.download_comfy(output["url"]))
            return BackendResult(tuple(images), receipt={"backend": self.backend, "task_id": locator["task_id"], "response_received": True})
        finally:
            await client.close()

    async def close(self):
        self._closed = True
