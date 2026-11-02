"""Plain-object companion.native-provider.v1 port; no dependency on V2 core types."""
from __future__ import annotations

import asyncio
import json
import time

from .owner import NativeImageOwner, checked
from .output_configuration import MAX_TASK_TIMEOUT_SECONDS, task_timeout_seconds as derive_task_timeout_seconds
from .package import package_hash
from .references import (EDIT_REFERENCE_ROLES, ReferenceSelectionError, edit_role,
                         select_edit_tickets)
from .types import (MAX_REFERENCE_IMAGES, ImageInvocation, NativeImageError, ReferenceImage,
                    ReferenceOffer, document, fingerprint, require_text)
from .waiting import deadline_timestamp, wait_for_output


SCHEMA = "companion.native-provider.v1"
PLUGIN_ID = "astrbot_plugin_private_companion.image"
PROVIDER_ID = "image.native"
REFERENCE_ROLES = frozenset((*EDIT_REFERENCE_ROLES, "edit_mask"))
IMAGE_OPERATIONS = ("generate", "edit", "mask_edit")


def scope_key(context: dict) -> str:
    # A policy revision is checked by the host guard, not a different identity.
    identity = json.loads(document(context))
    actor = identity.get("actor")
    if isinstance(actor, dict):
        actor.pop("policy_revision", None)
    return fingerprint(identity)


class NativeImageProvider:
    def __init__(self, owner: NativeImageOwner, *, package_version: str = "0.5.1", artifact_hash: str | None = None,
                 wait_budget_seconds: float = 300, poll_interval_seconds: float = 0.25,
                 now=time.time, monotonic=time.monotonic, sleep=asyncio.sleep):
        self.owner = owner
        self.version = require_text(package_version, "package_version")
        self.artifact_hash = artifact_hash or package_hash()
        self._closed = False
        self._tasks = set()
        if wait_budget_seconds < 0 or poll_interval_seconds <= 0:
            raise NativeImageError("invalid_native_wait_budget")
        # The per-task budget has exactly one source: the saved endpoint/local
        # configuration.  The constructor value (bootstrap reads a legacy
        # top-level key) may be stale and must never shorten it.
        self.wait_budget_seconds = max(wait_budget_seconds, self.task_timeout_seconds())
        self.poll_interval_seconds = poll_interval_seconds
        self.now, self.monotonic, self.sleep = now, monotonic, sleep

    def task_timeout_seconds(self) -> int:
        """Live time budget for a new image task, before anything is dispatched.

        The catalog declares only the documented ceiling; the effective budget
        is derived from the current saved configuration so a save applies to the
        next task without re-registering an immutable tool definition.
        """
        config = getattr(self.owner.backend, "config", None)
        return derive_task_timeout_seconds(config if isinstance(config, dict) else {})

    def _live(self):
        if self._closed:
            raise NativeImageError("image_provider_stopped")
        self.owner._live()

    def manifest(self) -> dict:
        self._live()
        tools = []
        for operation in IMAGE_OPERATIONS:
            properties = {"prompt": {"type": "string"}, "negative_prompt": {"type": "string"}}
            required = ["prompt"]
            if operation == "edit":
                properties["artifact_id"] = {"type": "string", "description": "可选：编辑本身份与当前会话范围内 Image 已保存的原图；留空时使用当前轮私聊或群聊消息中的图片。不能填写路径。"}
                properties["reference_ids"] = {"type": "array", "items": {"type": "string"},
                    "description": "从当前角色的 V2 参考目录中选择素材 ID；群聊使用还需打开角色的群聊参考图开关。"}
                properties["reference_indices"] = {"type": "array", "items": {"type": "integer"},
                    "description": "可选：按 0 起始编号精确选择本轮私聊或群聊附图；省略表示使用全部附图，填写 [] 表示只使用 reference_ids。"}
                properties["reference_roles"] = {"type": "array",
                    "items": {"type": "string", "enum": list(EDIT_REFERENCE_ROLES)},
                    "description": "按 reference_indices 的选择顺序、再按 reference_ids 顺序指定用途；必须属于素材声明的角色用途。"}
            elif operation == "mask_edit":
                properties["reference_ids"] = {"type": "array", "items": {"type": "string"},
                    "description": "可选的 V2 角色素材 ID；用途必须分别声明为 edit_source 或 edit_mask。"}
                properties["source_index"] = {"type": "integer", "description": "本轮图片顺序中的底图序号；先排当前私聊图片，再排 reference_ids。"}
                properties["mask_index"] = {"type": "integer", "description": "本轮图片顺序中的蒙版序号；先排当前私聊图片，再排 reference_ids。"}
                required.extend(("source_index", "mask_index"))
            tools.append({"action": "image." + operation, "capability": "image." + operation,
                          "source_owner": PROVIDER_ID,
                          "description": ({"generate": "生成图片", "edit": "编辑已保存原图或当前私聊输入图片",
                                           "mask_edit": "使用明确底图与蒙版进行局部编辑，蒙版外像素由 V2 按原图保护"}[operation]
                                          ) + "并返回原操作与产物回执；采用和发送由 V2 负责。",
                          "parameters": {"type": "object", "properties": properties,
                                         "required": required, "additionalProperties": False},
                          "side_effect": "external_network",
                          # The declaration is the immutable catalog identity of
                          # a session; it must not embed a mutable configured
                          # budget.  The per-task budget is applied live above.
                          "timeout_seconds": MAX_TASK_TIMEOUT_SECONDS})
        return {"schema": SCHEMA, "plugin_id": PLUGIN_ID, "provider_id": PROVIDER_ID,
                "generation": self.owner.generation,
                "package": {"extension_id": "image", "package_id": PLUGIN_ID,
                            "publisher_id": "private-companion-v2",
                            "package_version": self.version, "artifact_hash": self.artifact_hash,
                            "capabilities": [tool["capability"] for tool in tools], "dataset_owners": [PROVIDER_ID]},
                "tools": tools}

    def _request(self, request: dict) -> dict:
        self._live()
        value = json.loads(document(request))
        required = {"schema", "invocation_id", "parent_task_id", "tool_call_id", "action", "origin_key",
                    "context", "binding", "arguments", "arguments_hash", "deadline_at"}
        if not isinstance(value, dict) or required - value.keys() or value["schema"] != SCHEMA:
            raise NativeImageError("invalid_native_request")
        for name in ("invocation_id", "origin_key", "arguments_hash", "parent_task_id", "tool_call_id", "deadline_at"):
            require_text(value[name], name)
        deadline_timestamp(value["deadline_at"])
        binding = value["binding"]
        if (not isinstance(binding, dict) or binding.get("provider") != PROVIDER_ID
                or binding.get("version") != self.version or binding.get("generation") != self.owner.generation
                or binding.get("capability") != value["action"]):
            raise NativeImageError("native_provider_binding_mismatch")
        if value["action"] not in {"image." + op for op in getattr(self.owner.backend, "operations", ("generate", "edit"))}:
            raise NativeImageError("unsupported_image_operation")
        if (not isinstance(value["context"], dict) or not value["context"]
                or not isinstance(value["arguments"], dict)):
            raise NativeImageError("invalid_native_context")
        args = value["arguments"]
        operation = value["action"].split(".", 1)[1]
        allowed = {"prompt", "negative_prompt"}
        if operation == "edit":
            allowed.update(("artifact_id", "reference_ids", "reference_indices", "reference_roles"))
        elif operation == "mask_edit":
            allowed.update(("source_index", "mask_index", "reference_ids"))
        if args.keys() - allowed or "prompt" not in args:
            raise NativeImageError("invalid_image_arguments")
        require_text(args["prompt"], "prompt")
        if not isinstance(args.get("negative_prompt", ""), str):
            raise NativeImageError("invalid_negative_prompt")
        if operation == "edit" and "artifact_id" in args:
            require_text(args["artifact_id"], "artifact_id")
            if any(name in args for name in ("reference_roles", "reference_ids", "reference_indices")):
                raise NativeImageError("invalid_image_arguments")
        if "reference_ids" in args:
            references = args["reference_ids"]
            if (type(references) is not list or len(references) > MAX_REFERENCE_IMAGES
                    or any(type(item) is not str or not item or len(item) > 128 for item in references)
                    or len(set(references)) != len(references)):
                raise NativeImageError("invalid_reference_ids")
        if operation == "edit" and "reference_roles" in args:
            roles = args["reference_roles"]
            if (type(roles) is not list or not 1 <= len(roles) <= MAX_REFERENCE_IMAGES
                    or any(role not in EDIT_REFERENCE_ROLES for role in roles)):
                raise NativeImageError("invalid_reference_roles")
        if operation == "edit" and "reference_indices" in args:
            indices = args["reference_indices"]
            if (type(indices) is not list or len(indices) > MAX_REFERENCE_IMAGES
                    or any(type(index) is not int or not 0 <= index < MAX_REFERENCE_IMAGES
                           for index in indices)
                    or len(set(indices)) != len(indices)):
                raise NativeImageError("invalid_reference_indices")
        if operation == "mask_edit":
            if (any(type(args.get(name)) is not int or not 0 <= args[name] < MAX_REFERENCE_IMAGES
                    for name in ("source_index", "mask_index"))
                    or args["source_index"] == args["mask_index"]):
                raise NativeImageError("invalid_mask_reference_indices")
        return value

    @staticmethod
    def _same_request(request: dict, operation: dict):
        original = operation["request"]["envelope"]
        # A current instance may read a receipt from before restart, but cannot
        # reassign the original operation/invocation/arguments to a new task.
        fields = ("schema", "invocation_id", "parent_task_id", "tool_call_id", "action", "origin_key", "arguments", "arguments_hash")
        if any(request[key] != original.get(key) for key in fields):
            raise NativeImageError("native_original_invocation_mismatch")
        previous = original.get("binding", {})
        if any(request["binding"][key] != previous.get(key) for key in ("capability", "provider", "version")):
            raise NativeImageError("native_original_binding_mismatch")

    @staticmethod
    def _response(request: dict, operation: dict) -> dict:
        # The wire contract is exactly eleven keys (see
        # companion_v2.capabilities.native.parse_result).  ``failed`` means the
        # owner proved nothing was executed, so it maps to ``not_committed`` and
        # is safe to retry, while ``unknown`` stays ``unknown`` and is never
        # retried.  Redacted per-endpoint diagnostics stay in the operation
        # receipt for operators; adding them here would break every receipt.
        succeeded = operation["state"] == "succeeded"
        failed = operation["state"] in {"failed", "stopped"}
        state = "succeeded" if succeeded else "failed" if failed else "unknown"
        receipt = operation["receipt"]
        artifacts = [{"artifact_id": item["artifact_id"], "operation_id": operation["id"],
                      "content_hash": item["sha256"], "mime_type": item["media_type"], "size_bytes": item["size_bytes"]}
                     for item in receipt.get("artifacts", ())] if succeeded else []
        return {"schema": SCHEMA, "invocation_id": request["invocation_id"], "origin_key": request["origin_key"],
                "arguments_hash": request["arguments_hash"], "binding": request["binding"],
                "state": state, "effect": "committed" if succeeded else "not_committed" if failed else "unknown",
                "operation_id": operation["id"],
                "receipt_ref": receipt.get("receipt_id") or "image.native.operation:" + operation["id"],
                "result": {"operation_id": operation["id"], "operation": operation["request"]["operation"],
                           "artifact_ids": [item["artifact_id"] for item in artifacts],
                           "reference_inputs": operation["request"].get("references", []),
                           "original_provider_generation": operation["generation"],
                           "original_invocation_id": operation["request"]["invocation_id"],
                           "adopted": False, "delivered": False} if succeeded else None,
                "artifacts": artifacts}

    @staticmethod
    def _host_references(values):
        if type(values) not in (tuple, list) or len(values) > MAX_REFERENCE_IMAGES:
            raise NativeImageError("native_references_invalid")
        required = {"reference_handle", "reference_id", "source_key", "content_hash", "position"}
        result = []
        for item in values:
            keys = set(item) if type(item) is dict else set()
            if (type(item) is not dict or keys not in (required, required | {"roles"})
                    or any(type(item[name]) is not str or not item[name] for name in
                           ("reference_handle", "reference_id", "source_key", "content_hash"))
                    or len(item["content_hash"]) != 64
                    or any(char not in "0123456789abcdef" for char in item["content_hash"])
                    or type(item["position"]) is not int or not 0 <= item["position"] < MAX_REFERENCE_IMAGES):
                raise NativeImageError("native_references_invalid")
            if "roles" in item:
                roles = item["roles"]
                if (type(roles) not in (tuple, list) or not roles or len(roles) > len(REFERENCE_ROLES)
                        or any(type(role) is not str or role not in REFERENCE_ROLES for role in roles)
                        or len(set(roles)) != len(roles)):
                    raise NativeImageError("native_references_invalid")
            result.append(dict(item))
        if (len({item["reference_handle"] for item in result}) != len(result)
                or len({item["reference_id"] for item in result}) != len(result)
                or len({item["position"] for item in result}) != len(result)):
            raise NativeImageError("native_references_invalid")
        return tuple(sorted(result, key=lambda item: item["position"]))

    async def execute(self, request: dict, *, before_dispatch, host_references=(), fetch_reference=None) -> dict:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            return await self._execute(request, before_dispatch=before_dispatch,
                                       host_references=host_references, fetch_reference=fetch_reference)
        finally:
            self._tasks.discard(task)

    async def _execute(self, request: dict, *, before_dispatch, host_references=(), fetch_reference=None) -> dict:
        value = self._request(request)
        # Snapshot the live budget once, before any I/O.  A dashboard save while
        # this task is running changes the next task only; it must never extend
        # or shorten the deadline this task already started with.
        budget_seconds = self.task_timeout_seconds()
        await checked(before_dispatch)
        scope = scope_key(value["context"])
        tickets = self._host_references(host_references)
        if tickets and not callable(fetch_reference):
            raise NativeImageError("native_reference_reader_unavailable")
        refs = ()
        reference_offers = ()
        resolver = None
        operation = value["action"].split(".", 1)[1]
        arguments = value["arguments"]
        requested_ids = arguments.get("reference_ids", [])
        inbound = tuple(item for item in tickets if "roles" not in item)
        saved_by_id = {item["reference_id"]: item for item in tickets if "roles" in item}
        if any(identifier not in saved_by_id for identifier in requested_ids):
            raise NativeImageError("native_reference_id_unavailable")
        saved = tuple(saved_by_id[identifier] for identifier in requested_ids)
        if operation == "edit" and "artifact_id" in arguments:
            artifact_id = arguments["artifact_id"]
            metadata, _ = self.owner.store.artifact(scope, artifact_id)
            original_operation = self.owner.store.get(scope, metadata["operation_id"])
            if (original_operation["state"] != "succeeded"
                    or original_operation["generation"] != metadata["provider_generation"]):
                raise NativeImageError("image_artifact_generation_mismatch")
            original = await self.owner.read_artifact(scope, artifact_id, origin_key=value["origin_key"],
                                                     provider_generation=original_operation["generation"], authorize=before_dispatch)
            refs = (ReferenceImage(artifact_id, original.data, metadata["operation_id"], metadata["sha256"]),)
        elif operation in {"edit", "mask_edit"}:
            if operation == "edit":
                try:
                    selected_tickets = select_edit_tickets(arguments, inbound, saved_by_id)
                except ReferenceSelectionError as error:
                    raise NativeImageError(error.code) from None
                if not selected_tickets:
                    raise NativeImageError("native_reference_missing")
                selected = []
                for item in selected_tickets:
                    try:
                        role = edit_role(arguments, selected_tickets, item)
                    except ReferenceSelectionError as error:
                        raise NativeImageError(error.code) from None
                    selected.append((item, role))
                selected = tuple(selected)
            elif operation == "mask_edit":
                selected_tickets = (*inbound, *saved)
                if max(arguments["source_index"], arguments["mask_index"]) >= len(selected_tickets):
                    raise NativeImageError("invalid_mask_reference_indices")
                source = selected_tickets[arguments["source_index"]]
                mask = selected_tickets[arguments["mask_index"]]
                if (("roles" in source and "edit_source" not in source["roles"])
                        or ("roles" in mask and "edit_mask" not in mask["roles"])):
                    raise NativeImageError("native_reference_role_unavailable")
                selected = (((source, "edit_source"), (mask, "edit_mask"))
                            if source is not None and mask is not None and source is not mask else ())
            else:
                selected = ()
            if selected and not callable(fetch_reference):
                raise NativeImageError("native_reference_reader_unavailable")
            reference_offers = tuple(ReferenceOffer(item["reference_id"], item["source_key"], item["content_hash"], role)
                                     for item, role in selected)
            if reference_offers:
                ticket_by_id = {offer.reference_id: (item, role)
                                for (item, role), offer in zip(selected, reference_offers)}

                async def resolver(requested):
                    resolved = []
                    for offer in requested:
                        item, role = ticket_by_id[offer.reference_id]
                        raw = await fetch_reference(item["reference_handle"], offer.sha256, role=role)
                        resolved.append(ReferenceImage(offer.reference_id, raw, offer.source_key, offer.sha256, role))
                    return tuple(resolved)
        envelope = {key: value[key] for key in ("schema", "invocation_id", "parent_task_id", "tool_call_id", "action",
                                               "origin_key", "context", "binding", "arguments", "arguments_hash", "deadline_at")}
        image_prompt = value.get("image_prompt")
        if image_prompt is not None:
            if (not isinstance(image_prompt, dict) or set(image_prompt) != {"positive", "negative"}
                    or not isinstance(image_prompt["positive"], str)
                    or not isinstance(image_prompt["negative"], str)):
                raise NativeImageError("invalid_image_prompt_preferences")
            envelope["image_prompt"] = image_prompt
        invocation = ImageInvocation(scope, value["invocation_id"], value["invocation_id"], value["origin_key"],
                                     operation, arguments["prompt"], negative_prompt=arguments.get("negative_prompt", ""),
                                     reference_ids=(tuple(ref.reference_id for ref in refs)
                                                    or tuple(offer.reference_id for offer in reference_offers)), envelope=envelope)
        operation = await self.owner.execute(invocation, references=refs, reference_offers=reference_offers,
                                             resolve_references=resolver, before_dispatch=before_dispatch)
        if operation["state"] == "pending":
            operation = await wait_for_output(self.owner, operation, authorize=before_dispatch,
                                              deadline=deadline_timestamp(value["deadline_at"]),
                                              budget_seconds=budget_seconds,
                                              interval_seconds=self.poll_interval_seconds,
                                              now=self.now, monotonic=self.monotonic, sleep=self.sleep)
        # Return the control receipt even if authority changed while the backend
        # was running. Artifact bytes and host adoption have their own guards;
        # a completed external effect must not be reclassified as unknown here.
        return self._response(value, operation)

    async def lookup(self, request: dict, *, before_dispatch) -> dict:
        value = self._request(request)
        await checked(before_dispatch)
        scope = scope_key(value["context"])
        operation = (self.owner.store.get(scope, value["operation_id"]) if value.get("operation_id")
                     else self.owner.store.by_request(scope, value["invocation_id"]))
        self._same_request(value, operation)
        if operation["state"] == "pending":
            operation = await self.owner.recover(scope, operation["id"], authorize=before_dispatch)
        return self._response(value, operation)

    async def read_artifact(self, request: dict, *, before_dispatch) -> bytes:
        value = self._request(request)
        await checked(before_dispatch)
        scope = scope_key(value["context"])
        operation = self.owner.store.get(scope, require_text(value.get("operation_id"), "operation_id"))
        self._same_request(value, operation)
        artifact_id = require_text(value.get("artifact_id"), "artifact_id")
        original = await self.owner.read_artifact(scope, artifact_id, origin_key=value["origin_key"],
                                                 provider_generation=operation["generation"], authorize=before_dispatch)
        if original.metadata["operation_id"] != operation["id"] or original.metadata["sha256"] != value.get("content_hash"):
            raise NativeImageError("native_artifact_binding_mismatch")
        await checked(before_dispatch)
        return original.data

    async def close(self):
        self._closed = True
        tasks = tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.owner.close()
