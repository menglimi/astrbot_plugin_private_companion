"""Adopt verified native image artifacts into governed conversation originals."""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageChops

from companion_v2.conversation.external_tools import ExternalToolResponse
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor, PromptImage, check_manifest
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.models import SourceRecord

from ..media import inspect_image
from .image_native.references import ReferenceSelectionError, edit_role, select_edit_tickets
from .image_native.types import (MAX_REFERENCE_IMAGES, NativeImageError,
                                 inspect_image as inspect_reference_image)


IMAGE_ACTIONS = frozenset({"image.generate", "image.edit", "image.mask_edit"})
IMAGE_REFERENCE_ROLES = frozenset({"identity", "outfit", "pose", "scene", "style", "background", "continuity",
                                   "edit_source", "edit_mask"})


@dataclass(frozen=True, slots=True)
class _ReferenceImageContent:
    content: bytes = field(repr=False)


def _inspect_role_reference(asset, raw):
    try:
        info = inspect_reference_image(raw)
    except NativeImageError as error:
        code = "reference_image_limit" if error.code == "image_size_limit" else "reference_image_invalid"
        raise TaskError(code, "saved role reference does not meet the native image input contract") from error
    if (info["sha256"] != asset.content_hash.sha256 or info["size_bytes"] != asset.size_bytes
            or info["media_type"] != asset.mime_type):
        raise TaskError("reference_fingerprint_mismatch", "saved reference bytes differ from their registration")
    return _ReferenceImageContent(raw)


class ImageReferenceLease:
    """One invocation's one-use references backed by current images and role assets."""

    def __init__(self, registry, invocation):
        self.registry, self.invocation = registry, invocation
        self.messages = getattr(registry.application, "messages", None)
        self._tickets, self._by_handle, self._used = [], {}, []
        if (self.messages is None or invocation.context.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP)):
            return
        from companion_v2.conversation.native_tool_sources import native_input_images, native_invocation, native_turn
        self.native = native_turn(invocation.parent_task_id)
        if self.native:
            native_invocation(registry.runtime, invocation)
            event = native_input_images(self.messages, invocation.context, invocation.parent_task_id, optional=True)
            self.batch_id, events = invocation.parent_task_id, (event,) if event is not None else ()
        else:
            row = registry.runtime.read_one("""SELECT batch_id FROM extension_tool_invocations
                WHERE invocation_id=? AND parent_task_id=? AND tool_call_id=?""",
                (invocation.invocation_id, invocation.parent_task_id, invocation.tool_call_id))
            if row is None:
                return
            self.batch_id = row["batch_id"]
            batch = self.messages.generated_images._batch(self.batch_id, invocation.context, registry.now())
            _, _, events = self.messages.generated_images._inputs(batch, invocation.context, registry.now())
        for event in events:
            from companion_v2.conversation.media.models import ImageConversationEvent
            if not isinstance(event, ImageConversationEvent):
                continue
            views = self.messages.images.read(invocation.context, (event,), registry.now())
            for view in views:
                if type(view) is not PromptImage or type(view.descriptor) is not ImageDescriptor:
                    continue
                raw = base64.b64decode(view.data_base64, validate=True)
                captured = CapturedImage(view.descriptor, raw)
                reference, asset = self.messages.images.references(event, view.descriptor)
                if view.source != reference:
                    raise TaskError("native_reference_binding_mismatch", "input image source differs from its manifest")
                position = len(self._tickets)
                if position >= MAX_REFERENCE_IMAGES:
                    raise TaskError("native_reference_limit", "the current turn has too many image references")
                handle = uuid4().hex
                reference_id = "inbound-image-" + digest([invocation.invocation_id, reference, position])[:40]
                ticket = {
                    "reference_handle": handle,
                    "reference_id": reference_id,
                    "source_key": reference.source_id,
                    "content_hash": view.descriptor.content_hash,
                    "position": position,
                    "event": event,
                    "descriptor": view.descriptor,
                    "reference": reference,
                    "asset": asset,
                    "parent": event.source,
                    "image": captured,
                    "used": False,
                    "role": None,
                }
                self._tickets.append(ticket)
                self._by_handle[handle] = ticket
        if invocation.descriptor.tool.action in {"image.edit", "image.mask_edit"}:
            arguments = json.loads(invocation.arguments_json)
            reference_ids = arguments.get("reference_ids", [])
            if (type(reference_ids) is not list or len(reference_ids) > MAX_REFERENCE_IMAGES
                    or any(type(item) is not str or not item or len(item) > 128 for item in reference_ids)
                    or len(set(reference_ids)) != len(reference_ids)):
                raise TaskError("reference_ids_invalid", "saved reference IDs must be a unique bounded list")
            references = getattr(registry.application, "references", None)
            for asset_id in reference_ids:
                if references is None:
                    raise TaskError("reference_media_unavailable", "the V2 reference library is unavailable")
                position = len(self._tickets)
                if position >= MAX_REFERENCE_IMAGES:
                    raise TaskError("native_reference_limit", "the current edit has too many image references")
                asset, raw, roles = references.read_asset(invocation.context, asset_id)
                image = _inspect_role_reference(asset, raw)
                handle = uuid4().hex
                ticket = {
                    "reference_handle": handle,
                    "reference_id": asset.asset_id,
                    "source_key": "role-reference:" + asset.asset_id + ":" + str(asset.revision),
                    "content_hash": asset.content_hash.sha256,
                    "position": position,
                    "event": None,
                    "reference": None,
                    "asset": None,
                    "parent": None,
                    "image": image,
                    "used": False,
                    "role": None,
                    "roles": roles,
                    "registered_asset": asset,
                }
                self._tickets.append(ticket)
                self._by_handle[handle] = ticket

    @property
    def offers(self):
        offers = []
        for ticket in self._tickets:
            offer = {key: ticket[key] for key in
                     ("reference_handle", "reference_id", "source_key", "content_hash", "position")}
            if ticket.get("registered_asset") is not None:
                offer["roles"] = tuple(role.value if hasattr(role, "value") else role
                                        for role in ticket["roles"] if role in IMAGE_REFERENCE_ROLES)
            offers.append(offer)
        return tuple(offers)

    def _expected_role(self, ticket):
        action = self.invocation.descriptor.tool.action
        if action == "image.edit":
            arguments = json.loads(self.invocation.arguments_json)
            inbound = tuple(item for item in self._tickets if "roles" not in item)
            saved = {item["reference_id"]: item for item in self._tickets if "roles" in item}
            try:
                selected = select_edit_tickets(arguments, inbound, saved)
                return edit_role(arguments, selected, ticket)
            except ReferenceSelectionError as error:
                raise TaskError(error.code, "the requested image references do not match this invocation") from None
        arguments = json.loads(self.invocation.arguments_json)
        if ticket["position"] == arguments.get("source_index"):
            role = "edit_source"
        elif ticket["position"] == arguments.get("mask_index"):
            role = "edit_mask"
        else:
            return None
        if ticket.get("registered_asset") is not None and role not in ticket["roles"]:
            return None
        return role

    def _recheck(self, ticket, at):
        messages, context = self.messages, self.invocation.context
        registered = ticket.get("registered_asset")
        if registered is not None:
            current, payload, roles = self.registry.application.references.read_asset(
                context, registered.asset_id)
            if (current.revision != registered.revision or current.media_ref != registered.media_ref
                    or current.content_hash != registered.content_hash or payload != ticket["image"].content
                    or ticket["role"] not in roles):
                raise TaskError("reference_asset_changed", "the saved role reference changed during image editing")
            return
        if self.native:
            from companion_v2.conversation.native_tool_sources import native_input_images, native_invocation
            native_invocation(messages.runtime, self.invocation)
            if native_input_images(messages, context, self.batch_id) != ticket["event"]:
                raise TaskError("message_withdrawn", "the native image no longer belongs to its original turn")
        else:
            batch = messages.generated_images._batch(self.batch_id, context, at)
            row = messages.runtime.read_one("SELECT event_json,event_hash,withdrawn FROM conversation_inputs WHERE event_key=?",
                                            (ticket["event"].scoped_key,))
            if (row is None or row["withdrawn"] or row["event_json"] != encode(ticket["event"])
                    or row["event_hash"] != digest([ticket["event"], context.key, batch["role_instruction"]])):
                raise TaskError("message_withdrawn", "the input image is no longer part of its original active turn")
        parent = messages._read_source(ticket["parent"], context, ticket["event"].capability, at)
        if parent.content_hash != ticket["event"].input_hash:
            raise TaskError("message_input_changed", "the original input image source has changed")
        image_record = messages._read_source(ticket["reference"], context, ticket["event"].capability, at)
        payload = messages.images.metadata(ticket["event"], ticket["descriptor"])
        if image_record.payload != payload or image_record.content_hash != digest(payload):
            raise TaskError("image_content_changed", "the input image manifest has changed")
        asset_record = require(messages.images.assets.read(ticket["asset"], resolved=context.actor,
            capability=ticket["event"].capability, observed_at=at, authorizer=messages.tasks.identity))
        if not set(context.audience.recipients).issubset(asset_record.audience.recipients):
            raise TaskError("image_audience_denied", "the input image is no longer available to this audience")
        stored = messages.runtime.read_one("""SELECT * FROM conversation_image_payloads
            WHERE parent_id=? AND parent_lineage=? AND parent_revision=? AND position=?""",
            (ticket["parent"].source_id, ticket["parent"].version.lineage_id,
             ticket["parent"].version.revision, ticket["descriptor"].position))
        if (stored is None or stored["source_id"] != ticket["reference"].source_id
                or stored["source_lineage"] != ticket["reference"].version.lineage_id
                or stored["source_revision"] != ticket["reference"].version.revision
                or stored["descriptor_json"] != encode(ticket["descriptor"])
                or hashlib.sha256(stored["content"]).hexdigest() != ticket["content_hash"]):
            raise TaskError("image_content_changed", "the original input image bytes have changed")

    def recheck(self):
        if self.messages is None:
            return
        at = self.registry.now()
        for ticket in self._used:
            self._recheck(ticket, at)

    def fetch(self, handle, content_hash, *, role):
        ticket = self._by_handle.get(handle)
        if ticket is None:
            raise TaskError("reference_handle_invalid", "the image reference handle is not part of this invocation")
        if ticket["used"]:
            raise TaskError("reference_handle_consumed", "the image reference handle can be used only once")
        if (content_hash != ticket["content_hash"] or role != self._expected_role(ticket)
                or role not in IMAGE_REFERENCE_ROLES):
            raise TaskError("reference_binding_mismatch", "the requested image reference differs from this invocation")
        if self.messages is None:
            raise TaskError("reference_handle_invalid", "the original private image is unavailable")
        ticket["used"], ticket["role"] = True, role
        self._used.append(ticket)
        self._recheck(ticket, self.registry.now())
        return ticket["image"].content

    @property
    def used_references(self):
        return tuple(self._used)

    @property
    def dependencies(self):
        refs = []
        for ticket in self._used:
            refs.extend(reference for reference in (ticket["reference"], ticket["parent"])
                         if isinstance(reference, SourceRef))
        return tuple(dict.fromkeys(refs))


def _decoded_mask_image(raw, code):
    try:
        image = Image.open(BytesIO(raw))
        image.load()
        if (getattr(image, "n_frames", 1) != 1 or image.getexif().get(274, 1) not in (None, 1)
                or image.mode not in {"RGB", "L"}):
            raise ValueError("unsupported image mode")
        return image.copy()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise TaskError(code, "masked editing requires a single-frame RGB or grayscale image") from error


def _protect_masked_output(source, mask, output):
    source_image = _decoded_mask_image(source.content, "mask_source_invalid")
    mask_image = _decoded_mask_image(mask.content, "edit_mask_invalid")
    generated = _decoded_mask_image(output, "mask_output_invalid")
    if source_image.size != mask_image.size:
        raise TaskError("mask_dimension_mismatch", "the source image and mask must have identical dimensions")
    if generated.size != source_image.size:
        raise TaskError("mask_output_dimension_mismatch", "the generated image dimensions must match the source")
    if mask_image.mode == "RGB":
        red, green, blue = mask_image.split()
        if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
            raise TaskError("edit_mask_invalid", "RGB mask channels must agree")
        mask_l = red
    else:
        mask_l = mask_image
    low, high = mask_l.getextrema()
    if low != 0 or high == 0:
        raise TaskError("edit_mask_empty", "the mask must include both edited and preserved pixels")
    final = Image.composite(generated.convert("RGB"), source_image.convert("RGB"), mask_l)
    stream = BytesIO()
    final.save(stream, format="PNG")
    return stream.getvalue()


def image_result_handler(invocation, native_result, *, observed_at=None, input_references=()):
    """Keep the original receipt; only declared image actions can offer images."""
    images = []
    action = invocation.descriptor.tool.action
    if native_result.artifacts and action not in IMAGE_ACTIONS:
        raise TaskError("native_artifact_unadopted", "this action has no image adoption contract")
    if native_result.state == "succeeded" and action in IMAGE_ACTIONS and not native_result.artifacts:
        raise TaskError("native_artifact_unavailable", "successful image generation requires its original artifact")
    protection = []
    action = invocation.descriptor.tool.action
    used = tuple(input_references)
    source_reference = next((item for item in used if item.get("role") == "edit_source"), None)
    mask_reference = next((item for item in used if item.get("role") == "edit_mask"), None)
    if action == "image.mask_edit" and (source_reference is None or mask_reference is None
            or sum(item.get("role") == "edit_source" for item in used) != 1
            or sum(item.get("role") == "edit_mask" for item in used) != 1):
        raise TaskError("mask_references_unavailable", "masked editing requires one verified source and one verified mask")
    for position, artifact in enumerate(native_result.artifacts):
        image = inspect_image(artifact.content, position)
        if (type(image.descriptor) is not ImageDescriptor
                or image.descriptor.mime_type != artifact.mime_type
                or image.descriptor.content_hash != artifact.content_hash
                or image.descriptor.byte_count != artifact.size_bytes):
            raise TaskError("native_image_mismatch", "native image differs from the original static artifact receipt")
        if action == "image.mask_edit":
            final = _protect_masked_output(source_reference["image"], mask_reference["image"], artifact.content)
            final_image = inspect_image(final, position)
            protection.append({"algorithm": "source-mask.v1",
                "source_sha256": source_reference["content_hash"], "mask_sha256": mask_reference["content_hash"],
                "backend_sha256": artifact.content_hash, "final_sha256": final_image.descriptor.content_hash,
                "outside_mask": "exact_rgb"})
            image = final_image
        images.append(image)
    check_manifest(tuple(image.descriptor for image in images), allow_empty=True)
    source = None
    if native_result.state == "succeeded":
        payload_data = {"native": native_result.metadata(invocation.descriptor.binding)}
        if protection:
            payload_data["image_protection"] = protection
        registered = tuple({
            "asset_id": item["registered_asset"].asset_id,
            "revision": item["registered_asset"].revision,
            "content_hash": item["registered_asset"].content_hash.sha256,
            "role": item["role"],
        } for item in used if item.get("registered_asset") is not None)
        if registered:
            payload_data["reference_assets"] = registered
        payload = encode(payload_data)
        reference = SourceRef("native:" + invocation.invocation_id, invocation.descriptor.source_owner,
            invocation.context.scope, VersionRef(invocation.invocation_id, 1),
            observed_at or datetime.now(timezone.utc))
        source = SourceRecord(reference, invocation.context.audience, digest(payload), payload)
    return ExternalToolResponse(invocation.invocation_id, invocation.context.key, invocation.arguments_hash,
        invocation.descriptor.binding, native_result.state, native_result.effect, source=source,
        operation_id=native_result.operation_id, receipt_ref=native_result.receipt_ref, images=tuple(images),
        dependencies=tuple(dict.fromkeys(reference for item in used
            for reference in (item.get("reference"), item.get("parent"))
            if isinstance(reference, SourceRef))))
