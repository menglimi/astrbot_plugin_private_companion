"""Governed provider image originals, separate from user message intake."""

from dataclasses import dataclass, field
from datetime import datetime
import base64
import json
from types import SimpleNamespace

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, ScopeRef, SourceRef, VersionRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.sharing.models import ShareMaterial, ShareMaterialKind
from companion_v2.sources.dependencies import key, link
from companion_v2.sources.models import AssetRecord, GovernanceState, SourceRecord

from .models import (
    CapturedImage, ImageDescriptor, PromptImage, check_manifest, fits_budget,
    MAX_TURN_BYTES, MAX_TURN_FRAMES, MAX_TURN_IMAGES,
)
from .service import ConversationImages
from .video.models import fits_video_budget


OWNER = "conversation.generated_images"
VERSION = "conversation.generated_images.v1"
IMAGE_ACTIONS = frozenset({"image.generate", "image.edit", "image.mask_edit"})
GROUP_IMAGE_ACTIONS = frozenset({"image.generate", "image.edit"})
REJECTED_ADOPTION = "image_adoption_unavailable:"
INSTRUCTION = (
    "标记 generated 的图片是本轮能力提供方返回的生成或编辑产物，不是用户发来的照片，也不证明真实事件。edited 和 provider_operation 记录原操作归因，不据此声称人工原创、真实拍摄或第三方认可；产物已保存不等于角色已采用或已经发送，只有最终选择 sendable_image 后才进入授权投递。"
)


@dataclass(frozen=True)
class GeneratedImageView:
    """Internal image-source view; deliberately not an inbound event."""

    source: SourceRef
    principal: AuthenticatedHostPrincipal
    scope: ScopeRef
    capability: str
    input_hash: str
    images: tuple[ImageDescriptor, ...]
    image_attribution: dict


@dataclass(frozen=True)
class GeneratedAlbumImage:
    image_id: str
    created_at: str
    action: str
    descriptor: ImageDescriptor
    content: bytes = field(repr=False)
    parent: SourceRef | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _AlbumView:
    """给 ``ConversationImages.references`` 用的最小视图：它只读来源与作用域。

    生成时的完整视图（``_view``）还带归因与描述符清单，那些用于**发布元数据**；
    而图片引用的 id 只由 ``(来源, 位置, 内容哈希)`` 决定，所以重新拼引用时
    这几项就够了——相等性由 ``tests/test_generated_album_share.py`` 钉住。
    """

    source: SourceRef
    principal: object
    scope: ScopeRef


@dataclass(frozen=True)
class _GeneratedPromptImage(PromptImage):
    manifest_payload: str = field(repr=False)
    metadata_payload: str = field(repr=False)
    first_in_manifest: bool


class GeneratedImages:
    def __init__(self, messages):
        self.messages, self.runtime = messages, messages.runtime

    @staticmethod
    def reference(row, context):
        identifier = "generated-images-" + digest(row["invocation_id"])
        return SourceRef(identifier, OWNER, context.scope, VersionRef(identifier, 1),
                         datetime.fromisoformat(row["created_at"]))

    @staticmethod
    def _documents(value):
        """Find only complete governed references in already sealed documents."""
        if isinstance(value, dict):
            if "contribution_id" in value and value.get("state") != "ready":
                return
            if {"source_id", "owner", "scope", "version", "issued_at", "expires_at"}.issubset(value):
                from companion_v2.conversation.messages import source_from_document
                yield source_from_document(value)
            else:
                for child in value.values():
                    yield from GeneratedImages._documents(child)
        elif isinstance(value, list):
            for child in value:
                yield from GeneratedImages._documents(child)

    def _batch(self, batch, context, at):
        row = self.messages.batch(batch if isinstance(batch, str) else batch["batch_id"])
        self.messages.authorize(context, at)
        original = TaskContext.restore(json.loads(row["context_json"]))
        # Policy revisions advance as grants change. The stable key seals the
        # account binding, persona and audience; authorize rechecks current policy.
        if row["origin_key"] != context.key or original.key != context.key:
            raise TaskError("generated_image_context", "generated images retain their original conversation context")
        return row

    @staticmethod
    def _album_cursor(value):
        if value is None:
            return None
        if type(value) is not str or not 8 <= len(value) <= 1024:
            raise TaskError("generated_album_cursor_invalid", "the album cursor is invalid")
        try:
            padding = "=" * (-len(value) % 4)
            document = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
            if (type(document) is not dict or set(document) != {"created_at", "invocation_id", "position"}
                    or type(document["created_at"]) is not str
                    or type(document["invocation_id"]) is not str
                    or type(document["position"]) is not int or not 0 <= document["position"] < MAX_TURN_IMAGES):
                raise ValueError("invalid cursor")
            datetime.fromisoformat(document["created_at"])
            return document
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            raise TaskError("generated_album_cursor_invalid", "the album cursor is invalid") from None

    @staticmethod
    def _encode_album_cursor(item):
        value = json.dumps({"created_at": item.created_at, "invocation_id": item.image_id.split(":")[1],
                            "position": item.descriptor.position}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def album_refs(item, context):
        """一条相册图对应的三个引用：父产出、图片自身的 source、它的 asset。

        **与生成时发布的那三个引用逐字一致**，因为图片引用的 id 只由
        ``(父 source, 位置, 内容哈希)`` 决定（见 ``ConversationImages.references``）。
        这里一个字节都不写：生成图在落库时就已登记为受治理的 source 与 asset，
        这个方法只是把它们重新拼出来，供引用（发说说的来源）与分享素材使用。
        """
        try:
            _, invocation_id, _ = item.image_id.split(":", 2)
        except (AttributeError, ValueError):
            raise TaskError("generated_image_missing", "the selected album image is unavailable") from None
        if not invocation_id or len(invocation_id) > 128:
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        identifier = "generated-images-" + digest(invocation_id)
        parent = item.parent or SourceRef(identifier, OWNER, context.scope, VersionRef(identifier, 1),
                                          datetime.fromisoformat(item.created_at))
        source, asset = ConversationImages.references(
            _AlbumView(parent, context.actor.principal, context.scope), item.descriptor)
        return parent, source, asset

    @staticmethod
    def album_share_material(item, context):
        """相册图 → 分享素材：来源与素材正本都指向**已经发布**的那两个引用。

        角色自有素材必须声明它的 bot/人格（分享域的规则），所以这里带上
        ``RoleOwnerRef``——人格取**角色主体**，与关系记忆、风格样本用的是同一个来源；
        素材与来源同域这一条由模型自己再校验一次。
        """
        _, source, asset = GeneratedImages.album_refs(item, context)
        return ShareMaterial(
            material_id="generated-material-" + digest([source.source_id, asset.asset_id]),
            kind=ShareMaterialKind.ROLE_ARTIFACT,
            source=source,
            asset=asset,
            created_at=datetime.fromisoformat(item.created_at),
            role_owner=RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                    context.role_binding.role.subject_id),
        )

    @staticmethod
    def album_share_candidate(item, context, *, planner, capability, at, authorizer):
        """相册图 → 分享候选：**经 ``SharePlanner`` 复检**之后才算数。

        复检不是走过场：它真的去读源仓库与素材仓库（凭身份授权），所以页面或模型都拿不到
        一个"图库里并不存在"的来源。失败原样返回，不在这里编一个能过的素材。
        """
        material = GeneratedImages.album_share_material(item, context)
        return planner.prepare(material, resolved=context.actor, capability=capability, observed_at=at,
                               authorizer=authorizer, role_owner=material.role_owner)

    def album_page(self, context, at, *, limit=24, cursor=None):
        """Read accepted Image output as a role-scoped, non-owning album projection."""
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 32:
            raise TaskError("generated_album_limit_invalid", "the album page size is outside its bounds")
        before = self._album_cursor(cursor)
        self.messages.authorize(context, at)
        parameters = [context.key]
        where = ["b.origin_key = ?", "b.state = 'ready'", "i.action IN (?,?,?)", "i.state = 'succeeded'"]
        parameters.extend(sorted(IMAGE_ACTIONS))
        if before is not None:
            where.append("(i.created_at < ? OR (i.created_at = ? AND i.invocation_id <= ?))")
            parameters.extend((before["created_at"], before["created_at"], before["invocation_id"]))
        rows = self.runtime.read_all(
            """SELECT i.invocation_id, i.batch_id, i.action, i.created_at
               FROM extension_tool_invocations i JOIN conversation_batches b ON b.batch_id=i.batch_id
               WHERE """ + " AND ".join(where) + " ORDER BY i.created_at DESC, i.invocation_id DESC LIMIT 128",
            tuple(parameters))
        from .native_generated_album import page as native_page
        native_items, native_more = native_page(self, context, at, before=before, limit=limit)
        by_batch = {}
        items = []
        for row in rows:
            if row["batch_id"] not in by_batch:
                by_batch[row["batch_id"]] = self.read_batch(row["batch_id"], context, at)
            reference = self.reference(row, context)
            for image in by_batch[row["batch_id"]]:
                if image.parent.source_id != reference.source_id:
                    continue
                item = GeneratedAlbumImage(
                    "generated-image:" + row["invocation_id"] + ":" + str(image.descriptor.position),
                    row["created_at"], row["action"], image.descriptor,
                    base64.b64decode(image.data_base64, validate=True))
                if before is not None and (item.created_at, row["invocation_id"], image.descriptor.position) >= (
                        before["created_at"], before["invocation_id"], before["position"]):
                    continue
                items.append(item)
            if len(items) >= limit + 1:
                break
        items.extend(native_items)
        items.sort(key=lambda item: (item.created_at, item.image_id.split(":")[1], item.descriptor.position),
                    reverse=True)
        more = len(items) > limit or len(rows) == 128 or native_more
        page = tuple(items[:limit])
        next_cursor = self._encode_album_cursor(page[-1]) if page and more else None
        return {"items": page, "next_cursor": next_cursor}

    def observability_page(self, context, at, *, limit=20):
        """Read safe image invocation outcomes for the exact selected private route."""
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise TaskError("generated_observability_limit_invalid", "the image log page size is outside its bounds")
        self.messages.authorize(context, at)
        actions = tuple(sorted(IMAGE_ACTIONS))
        where = "b.origin_key = ? AND i.action IN (?,?,?)"
        parameters = (context.key, *actions)
        count = self.runtime.read_one(
            """SELECT COUNT(*) AS total FROM extension_tool_invocations i
               JOIN conversation_batches b ON b.batch_id=i.batch_id WHERE """ + where,
            parameters)
        rows = self.runtime.read_all(
            """SELECT i.action, i.state, i.error_code, i.created_at, i.finished_at, i.task_id
               FROM extension_tool_invocations i JOIN conversation_batches b ON b.batch_id=i.batch_id
               WHERE """ + where + " ORDER BY i.created_at DESC, i.invocation_id DESC LIMIT ?",
            (*parameters, limit))
        from .native_generated_album import diagnostics
        native_count, native_rows = diagnostics(self, context, limit=limit)
        rows = sorted([dict(row) for row in rows] + native_rows, key=lambda row: row["created_at"], reverse=True)[:limit]
        items = []
        from companion_v2.model_gateway.images import image_failure
        for row in rows:
            elapsed_ms = None
            if row["finished_at"]:
                try:
                    elapsed_ms = max(0, round((datetime.fromisoformat(row["finished_at"])
                        - datetime.fromisoformat(row["created_at"])).total_seconds() * 1000))
                except (TypeError, ValueError):
                    elapsed_ms = None
            items.append({"action": row["action"], "state": row["state"],
                          "error_code": (image_failure(self.runtime, row["task_id"])
                                         if row["state"] in ("failed", "unknown") else None) or row["error_code"],
                          "created_at": row["created_at"],
                          "finished_at": row["finished_at"], "elapsed_ms": elapsed_ms})
        return {"total": int(count["total"] if count is not None else 0) + native_count, "items": tuple(items)}

    def album_image(self, context, at, image_id):
        """Re-read one exact accepted image after checking its original context and receipt."""
        if type(image_id) is not str or len(image_id) > 180 or not image_id.startswith("generated-image:"):
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        try:
            _, invocation_id, raw_position = image_id.split(":", 2)
            position = int(raw_position)
        except (TypeError, ValueError):
            raise TaskError("generated_image_missing", "the selected album image is unavailable") from None
        if (not invocation_id or len(invocation_id) > 128 or not 0 <= position < MAX_TURN_IMAGES):
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        self.messages.authorize(context, at)
        row = self.runtime.read_one(
            """SELECT i.invocation_id, i.batch_id, i.action, i.created_at
               FROM extension_tool_invocations i JOIN conversation_batches b ON b.batch_id=i.batch_id
               WHERE i.invocation_id=? AND b.origin_key=? AND i.action IN (?,?,?) AND i.state='succeeded'""",
            (invocation_id, context.key, *sorted(IMAGE_ACTIONS)))
        if row is None:
            from .native_generated_album import image
            return image(self, context, at, invocation_id, position)
        reference = self.reference(row, context)
        for image in self.read_batch(row["batch_id"], context, at):
            if image.parent.source_id == reference.source_id and image.descriptor.position == position:
                return GeneratedAlbumImage(image_id, row["created_at"], row["action"], image.descriptor,
                                           base64.b64decode(image.data_base64, validate=True))
        raise TaskError("generated_image_missing", "the selected album image is no longer available")

    def _inputs(self, batch, context, at):
        from companion_v2.conversation.messages import event_from_document
        saved = json.loads(batch["contributions_json"])
        if not isinstance(saved, dict) or not isinstance(saved.get("inputs"), list):
            raise TaskError("generated_image_inputs", "generated images require the original sealed inputs")
        rows = self.runtime.read_all("SELECT * FROM conversation_inputs WHERE batch_id = ? ORDER BY position",
                                     (batch["batch_id"],))
        selected = [row for row in rows if row["event_key"] in saved["inputs"]]
        if (not selected or [row["event_key"] for row in selected] != saved["inputs"]
                or any(row["withdrawn"] for row in selected)):
            raise TaskError("message_withdrawn", "generated images depend on the original active inputs")
        references, original_hashes, events = {}, [], []
        for row in selected:
            event = event_from_document(json.loads(row["event_json"]), context)
            if (event.scope != context.scope or event.principal != context.actor.principal
                    or row["event_key"] != event.scoped_key
                    or row["event_hash"] != digest([event, context.key, batch["role_instruction"]])):
                raise TaskError("generated_image_inputs", "generated images cannot change their original sender or input")
            source = self.messages._read_source(event.source, context, event.capability, at)
            if source.content_hash != event.input_hash:
                raise TaskError("message_input_changed", "generated image input no longer matches its source")
            references[event.source] = event.capability
            original_hashes.append({"event_key": row["event_key"], "event_hash": row["event_hash"]})
            events.append(event)
            images = list(self.messages.images.read(context, (event,), at))
            if getattr(event, "quotes", ()):
                materials, quoted, _, _, _ = self.messages.quotes.read_all(context, event, at)
                references.update((m.source, event.capability) for m in materials if m.source is not None)
                images.extend(quoted)
            if getattr(event, "forwards", ()):
                materials, forwarded, _, _, _ = self.messages.forwards.read_all(context, event, at)
                references.update((m.source, event.capability) for m in materials if m.source is not None)
                images.extend(forwarded)
            for image in images:
                for view in image.evidence_images:
                    references[view.source] = event.capability
                    references[view.parent] = event.capability
        references.update((reference, "text") for reference in self._documents(saved))
        return original_hashes, references, events

    @staticmethod
    def _media(events, generated=()):
        from companion_v2.conversation.quotes.models import event_audios, event_images, event_videos
        images = tuple(image for event in events for image in event_images(event))
        audios = tuple(audio for event in events for audio in event_audios(event))
        videos = tuple(video for event in events for video in event_videos(event))
        return images + tuple(image.descriptor for image in generated), audios, videos

    def require_capacity(self, batch, context, at):
        """Do not dispatch an image operation when no output can fit this turn."""
        with self.runtime.read_snapshot():
            batch = self._batch(batch, context, at)
            _, _, events = self._inputs(batch, context, at)
            images, audios, videos = self._media(events, self.read_batch(batch, context, at))
            byte_count = sum(item.retained_bytes for item in (*images, *audios, *videos))
            frame_count = sum(image.rendered_count for image in images) + sum(len(v.frames) for v in videos)
            if (not fits_video_budget(videos, images, audios) or len(images) >= MAX_TURN_IMAGES
                    or byte_count >= MAX_TURN_BYTES or frame_count >= MAX_TURN_FRAMES):
                raise TaskError("image_turn_limit", "this turn has no remaining capacity for a generated image")

    @staticmethod
    def rejected(row):
        code = row["error_code"] or ""
        if row["action"] in IMAGE_ACTIONS and row["state"] == "succeeded" and code.startswith(REJECTED_ADOPTION):
            return {"state": "unavailable", "code": code[len(REJECTED_ADOPTION):]}
        return None

    def _manifest(self, row, context, at):
        """Validate controlled adoption metadata without re-entering the executor."""
        from companion_v2.conversation.messages import source_from_document
        reference = self.reference(row, context)
        record = self.messages._read_source(reference, context, "text", at)
        try:
            data = json.loads(record.payload)
            expected = self._binding(row, context, source_from_document(json.loads(row["result_source_json"])), row["result_hash"])
            expected.update(operation_id=row["operation_id"], receipt_ref=row["receipt_ref"], effect=row["effect"])
            if (record.content_hash != digest(record.payload) or record.audience != context.audience
                    or set(data) != set(expected) | {"inputs", "context_hash", "evidence", "history_evidence",
                                                   "previous", "images", "image_adoption"}
                    or any(encode(data[name]) != encode(value) for name, value in expected.items())
                    or data["image_adoption"] not in ({"state": "available", "code": None},
                                                       {"state": "unavailable", "code": "image_turn_limit"})):
                raise ValueError("changed manifest")
            descriptors = tuple(ImageDescriptor(**item) for item in data["images"])
            check_manifest(descriptors)
        except TaskError:
            raise
        except (ValueError, TypeError, KeyError):
            raise TaskError("generated_image_changed", "generated image manifest differs from its accepted originals") from None
        return reference, record, data, descriptors

    def adoption_status(self, row, context, at):
        if row["action"] not in IMAGE_ACTIONS or row["state"] != "succeeded":
            return None
        rejected = self.rejected(row)
        return rejected if rejected is not None else self._manifest(row, context, at)[2]["image_adoption"]

    def _evidence(self, references, context, at):
        evidence = []
        for reference, capability in references.items():
            source = self.messages._read_source(reference, context, capability, at)
            evidence.append({"source": reference, "capability": capability,
                             "content_hash": source.content_hash, "payload_hash": digest(source.payload)})
        return evidence

    def _history_evidence(self, batch, context, at):
        saved = json.loads(batch["contributions_json"])
        if context.scope.kind is not ScopeKind.GROUP or "group_context" not in saved:
            return ()
        # The group owner rechecks each original member's authorization. Do not
        # impersonate that member or grant the current member direct source read.
        return self.messages.group_context.replies.source_evidence(saved["group_context"], context, batch["batch_id"], at)

    def _external(self):
        external = getattr(self.messages.tools, "external", None)
        if external is None:
            raise TaskError("tool_owner_unavailable", "generated image originals require their registered provider")
        return external

    def _binding(self, row, context, result_source, result_hash):
        call = self.runtime.read_one("SELECT * FROM conversation_tool_calls WHERE task_id = ? AND tool_call_id = ?",
                                     (row["parent_task_id"], row["tool_call_id"]))
        if (call is None or (call["batch_id"], call["action"], call["arguments_json"]) != (
                row["batch_id"], row["action"], row["arguments_json"])):
            raise TaskError("generated_image_invocation", "image output requires its original ordered model tool call")
        return {"version": VERSION, "batch_id": row["batch_id"], "origin_key": context.key,
                "invocation_id": row["invocation_id"], "parent_task_id": row["parent_task_id"],
                "tool_call_id": row["tool_call_id"], "action": row["action"],
                "tool_sequence": call["sequence"], "model_call_id": call["model_call_id"],
                "attempt_id": call["attempt_id"],
                "descriptor_hash": digest(json.loads(row["descriptor_json"])),
                "provider": json.loads(row["provider_json"]),
                "arguments_hash": digest(json.loads(row["arguments_json"])),
                "result_source": result_source, "result_hash": result_hash}

    def _tool_sources(self, row, context, at):
        """Retain governed sources actually returned by earlier local tools."""
        from companion_v2.conversation.messages import source_from_document
        calls = self.runtime.read_all("""SELECT * FROM conversation_tool_calls WHERE batch_id = ?
            AND sequence < (SELECT sequence FROM conversation_tool_calls WHERE task_id = ? AND tool_call_id = ?)
            AND state = 'succeeded' ORDER BY sequence""", (row["batch_id"], row["parent_task_id"], row["tool_call_id"]))
        references = {}
        for call in calls:
            result = json.loads(call["result_json"])
            if call["action"] == "source.read":
                arguments = json.loads(call["arguments_json"])
                offer = self.runtime.read_one("SELECT * FROM conversation_tool_sources WHERE batch_id = ? AND handle = ?",
                                              (row["batch_id"], arguments["handle"]))
                if offer is None:
                    raise TaskError("tool_source_unavailable", "a generating tool requires its original source handle")
                reference = source_from_document(json.loads(offer["source_json"]))
                source = self.messages._read_source(reference, context, offer["capability"], at)
                if (result.get("state") != "succeeded" or result.get("content_hash") != source.content_hash
                        or result.get("payload_hash") != digest(source.payload)):
                    raise TaskError("tool_result_changed", "generated image source input differs from the actual tool result")
                references[reference] = offer["capability"]
            else:
                references.update((reference, "text") for reference in self._documents(result))
        return references

    def _previous(self, row, context, at):
        """Read earlier external originals without recursively compiling the turn."""
        records = self.runtime.read_all("""SELECT i.* FROM extension_tool_invocations i
            JOIN conversation_tool_calls c ON c.task_id = i.parent_task_id AND c.tool_call_id = i.tool_call_id
            WHERE i.batch_id = ? AND c.sequence < (SELECT sequence FROM conversation_tool_calls
                WHERE task_id = ? AND tool_call_id = ?) AND i.state = 'succeeded' ORDER BY c.sequence""",
            (row["batch_id"], row["parent_task_id"], row["tool_call_id"]))
        previous, references = [], []
        for earlier in records:
            result, material = self._external().snapshot(earlier, context, at)
            if result["state"] != "succeeded" or (material is None and self.rejected(earlier) is None
                    and earlier["action"] != "screen.capture"):
                raise TaskError("generated_image_dependency", "an earlier provider original is unresolved")
            previous.append({"invocation_id": earlier["invocation_id"], "result_hash": earlier["result_hash"]})
            if material is not None:
                references.append(material.source)
        return previous, references

    @staticmethod
    def _view(reference, context, payload):
        data = json.loads(payload)
        descriptors = tuple(ImageDescriptor(**item) for item in data["images"])
        check_manifest(descriptors)
        attribution = {"generated": True, "edited": data["action"] in {"image.edit", "image.mask_edit"},
                       "attribution": "provider_operation", "original_author": "unknown",
                       "sender": None, "origin": "native_provider_artifact",
                       "provider_operation": {name: data[name] for name in (
                           "invocation_id", "action", "operation_id", "receipt_ref", "provider")}}
        return GeneratedImageView(reference, context.actor.principal, context.scope, "text", digest(payload),
                                  descriptors, attribution)

    def accept(self, invocation, response):
        """Called inside the external source/receipt transaction by a trusted owner."""
        images = getattr(response, "images", ())
        if response.source is not None and response.source.state is GovernanceState.REVOKED:
            # A native owner may know the operation succeeded after its authority
            # expired. That receipt is retained by the executor, never adopted.
            return None
        if not images:
            return None
        if invocation.descriptor.tool.action not in IMAGE_ACTIONS:
            raise TaskError("generated_image_action", "image originals require the explicitly registered image action")
        if (not isinstance(images, tuple) or any(not isinstance(image, CapturedImage) for image in images)
                or any(type(image.descriptor) is not ImageDescriptor for image in images)):
            raise TaskError("generated_image_manifest", "native artifacts currently require captured static images")
        check_manifest(tuple(image.descriptor for image in images))
        if (response.state != "succeeded" or response.source is None or not response.operation_id or not response.receipt_ref
                or (response.invocation_id, response.origin_key, response.arguments_hash, response.binding) != (
                    invocation.invocation_id, invocation.context.key, invocation.arguments_hash, invocation.descriptor.binding)):
            raise TaskError("generated_image_receipt", "image publication requires the original successful provider receipt")
        context, at = invocation.context, self.messages.tasks.now()
        with self.runtime.transaction() as connection:
            row = self.runtime.read_one("SELECT * FROM extension_tool_invocations WHERE invocation_id = ?",
                                        (invocation.invocation_id,))
            if (row is None or row["state"] not in ("running", "unknown", "succeeded")
                    or (row["parent_task_id"], row["tool_call_id"], row["action"], row["descriptor_json"], row["provider_json"], row["arguments_json"]) != (
                    invocation.parent_task_id, invocation.tool_call_id, invocation.descriptor.tool.action,
                    encode(invocation.descriptor), encode(invocation.descriptor.binding), invocation.arguments_json)):
                raise TaskError("generated_image_invocation", "image publication belongs to the original invocation")
            batch = self._batch(row["batch_id"], context, at)
            if (batch["generation_task_id"] != invocation.parent_task_id
                    or response.source.reference.owner != invocation.descriptor.source_owner
                    or response.source.reference.scope != context.scope or response.source.audience != context.audience):
                raise TaskError("generated_image_context", "provider image result belongs to another original audience")
            require(self.messages.sources.validate_reference(response.source.reference, observed_at=at))
            stored = self.runtime.read_one("SELECT payload_json, content_hash FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?",
                                           key(response.source.reference))
            if (json.loads(stored["payload_json"]), stored["content_hash"]) != (response.source.payload, response.source.content_hash):
                raise TaskError("generated_image_result_changed", "provider image bytes require their published original result")
            inputs, references, events = self._inputs(batch, context, at)
            references.update(self._tool_sources(row, context, at))
            previous, previous_sources = self._previous(row, context, at)
            evidence = self._evidence(references, context, at)
            history_evidence = self._history_evidence(batch, context, at)
            history_sources = tuple(item["source"] for item in history_evidence)
            binding = self._binding(row, context, response.source.reference, digest(response.source))
            earlier = self.read_batch(batch, context, at, _before_sequence=binding["tool_sequence"])
            retained, audios, videos = self._media(events, earlier)
            available = fits_video_budget(videos, retained + tuple(image.descriptor for image in images), audios)
            adoption = {"state": "available" if available else "unavailable", "code": None if available else "image_turn_limit"}
            payload = encode({**binding,
                              "operation_id": response.operation_id, "receipt_ref": response.receipt_ref,
                              "effect": response.effect, "inputs": inputs,
                              "context_hash": digest(batch["contributions_json"]), "evidence": evidence,
                              "history_evidence": history_evidence,
                              "previous": previous, "images": [image.descriptor for image in images],
                              "image_adoption": adoption})
            reference = self.reference(row, context)
            require(self.messages.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload), observed_at=at))
            link(self.runtime, reference, (response.source.reference, *references, *history_sources, *previous_sources), at)
            if not available:
                return reference
            view = self._view(reference, context, payload)
            for image in images:
                source, asset = self.messages.images.references(view, image.descriptor)
                metadata = self.messages.images.metadata(view, image.descriptor)
                require(self.messages.sources.publish(SourceRecord(source, context.audience, digest(metadata), metadata), observed_at=at))
                link(self.runtime, source, (reference,), at)
                require(self.messages.images.assets.publish(AssetRecord(asset, context.audience), observed_at=at))
                values = (*key(reference), image.descriptor.position, *key(source), asset.asset_id,
                          asset.version.lineage_id, asset.version.revision, encode(image.descriptor), image.content)
                old = connection.execute("""SELECT * FROM conversation_image_payloads WHERE parent_id = ?
                    AND parent_lineage = ? AND parent_revision = ? AND position = ?""", values[:4]).fetchone()
                if old is not None and tuple(old) != values:
                    raise TaskError("image_version_conflict", "an accepted provider image cannot be replaced")
                connection.execute("INSERT OR IGNORE INTO conversation_image_payloads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            return reference

    def read_batch(self, batch, context, at, *, evidence=None, _before_sequence=None):
        """Recheck source, exact receipt and current provider grants at every use."""
        from companion_v2.conversation.messages import source_from_document
        images, accepted_evidence = [], []
        with self.runtime.read_snapshot():
            batch = self._batch(batch, context, at)
            rows = self.runtime.read_all("""SELECT i.*, c.sequence AS tool_sequence FROM extension_tool_invocations i
                JOIN conversation_tool_calls c ON c.task_id = i.parent_task_id AND c.tool_call_id = i.tool_call_id
                WHERE i.batch_id = ? ORDER BY c.sequence""", (batch["batch_id"],))
            for row in rows:
                if _before_sequence is not None and row["tool_sequence"] >= _before_sequence:
                    continue
                if self.rejected(row) is not None:
                    self._external().snapshot(row, context, at)
                    continue
                reference = self.reference(row, context)
                exists = self.runtime.read_one("SELECT 1 FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?", key(reference))
                if exists is None:
                    orphan = self.runtime.read_one("SELECT 1 FROM conversation_image_payloads WHERE parent_id = ? LIMIT 1", (reference.source_id,))
                    if orphan is not None or row["state"] == "succeeded" and row["action"] in IMAGE_ACTIONS:
                        raise TaskError("generated_image_unavailable", "successful image output has lost its governed manifest")
                    continue
                result, material = self._external().snapshot(row, context, at)
                if result["state"] != "succeeded" or material is None:
                    raise TaskError("generated_image_unresolved", "only an accepted successful child receipt exposes images")
                reference, record, data, descriptors = self._manifest(row, context, at)
                try:
                    if (data["context_hash"] != digest(batch["contributions_json"])
                            or material.source != source_from_document(data["result_source"])):
                        raise ValueError("changed manifest")
                    inputs, references, events = self._inputs(batch, context, at)
                    references.update(self._tool_sources(row, context, at))
                    if data["inputs"] != inputs or data["evidence"] != json.loads(encode(self._evidence(references, context, at))):
                        raise ValueError("changed original evidence")
                    history_evidence = self._history_evidence(batch, context, at)
                    if data["history_evidence"] != json.loads(encode(history_evidence)):
                        raise ValueError("changed original group history")
                    history_sources = tuple(item["source"] for item in history_evidence)
                    previous, previous_sources = self._previous(row, context, at)
                    if data["previous"] != previous:
                        raise ValueError("changed provider ancestry")
                    retained, audios, videos = self._media(events, images)
                    available = fits_video_budget(videos, retained + descriptors, audios)
                    if available != (data["image_adoption"]["state"] == "available"):
                        raise ValueError("changed adoption budget")
                    if not available:
                        accepted_evidence.extend((reference, material.source, *references, *history_sources, *previous_sources))
                        continue
                    view = self._view(reference, context, record.payload)
                except TaskError:
                    raise
                except (ValueError, TypeError, KeyError):
                    raise TaskError("generated_image_changed", "generated image manifest differs from its accepted originals") from None
                originals = self.messages.images.read(context, (view,), at)
                for index, image in enumerate(originals):
                    images.append(_GeneratedPromptImage(image.source, image.parent, image.descriptor, image.data_base64,
                        record.payload, self.messages.images.metadata(view, image.descriptor), index == 0))
                accepted_evidence.extend((reference, material.source, *references, *history_sources,
                                          *previous_sources, *(i.source for i in originals)))
            if not fits_budget(tuple(image.descriptor for image in images)):
                raise TaskError("image_turn_limit", "generated originals exceed this turn's shared image budget")
        if evidence is not None:
            evidence.extend(dict.fromkeys(accepted_evidence))
        return tuple(images)

    @staticmethod
    def contributions(image, *, purpose="generated_image:"):
        from companion_v2.conversation.foreground import ContextContribution, ContributionState
        if not isinstance(image, _GeneratedPromptImage):
            raise TaskError("generated_image_view", "generated contributions require the governed original read")
        result = []
        if image.first_in_manifest:
            result.append(ContextContribution(image.parent.source_id, OWNER, purpose + image.parent.source_id,
                ContributionState.READY, image.manifest_payload, image.parent, True))
        result.append(ContextContribution(image.source.source_id, image.source.owner,
            purpose + image.source.source_id, ContributionState.READY, image.metadata_payload, image.source, True))
        return tuple(result)

    def validate_refresh(self, batch, context, at, original, current):
        """Allow only this turn's currently governed provider images to be added."""
        from companion_v2.conversation.foreground import PromptMaterial
        from .screen import INSTRUCTION as SCREEN_INSTRUCTION
        generated = self.read_batch(batch, context, at)
        semantic = getattr(self.messages, "semantics", None)
        reactions, reaction_materials = semantic.reactions.read_batch(batch, context) if semantic else ((), ())
        baseline = SimpleNamespace(event=original.event, images=original.images + generated + reactions,
                                   videos=current.videos, audios=current.audios)
        screens = self.messages.screen_images.read_batch(batch, context, at, baseline)
        if current.images != baseline.images + screens:
            return False
        expected_reply = bool(baseline.images) and self.messages.reply_images.enabled and context.scope.kind in (
            ScopeKind.PRIVATE, ScopeKind.GROUP)
        if current.reply_images != expected_reply:
            return False
        expected = tuple(PromptMaterial(c.owner, c.purpose, c.payload, c.source)
                         for image in generated for c in self.contributions(image))
        references = {i.source for i in generated} | {i.parent for i in generated}
        actual = tuple(m for m in current.context.materials if m.source in references
                       or m.owner == OWNER or m.purpose.startswith("generated_image:"))
        actual_reactions = tuple(m for m in current.context.materials if m.owner == "expression.reactions")
        expected_screens = tuple(PromptMaterial(c.owner, c.purpose, c.payload, c.source)
            for image in screens for c in self.messages.screen_images.contributions(image))
        screen_refs = {i.source for i in screens}
        actual_screens = tuple(m for m in current.context.materials if m.source in screen_refs
                               or m.purpose.startswith("screen_capture:"))
        return (actual == expected and actual_reactions == reaction_materials
                and actual_screens == expected_screens
                and (not generated or INSTRUCTION in current.instruction)
                and (not screens or SCREEN_INSTRUCTION in current.instruction))
