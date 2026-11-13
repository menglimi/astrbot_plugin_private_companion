"""Dashboard management for role-owned image references and wardrobe records."""

from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
import os
import subprocess
import sys
import warnings
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageOps

from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.conversation.media.output_settings import effective_settings as image_settings, parse_scene_presets
from companion_v2.execution.models import TaskContext, TaskError, digest, require
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.references.configuration import (
    GROUP as REFERENCE_GROUP,
    ReferenceConfigurationOwner,
    configuration_fields as reference_fields,
)
from companion_v2.references.models import (
    MAX_ASSET_BYTES,
    ContentDigest,
    ReferenceAsset,
    ReferenceKind,
    ReferenceRole,
    ReferenceSource,
    ReferenceState,
    WardrobeItem,
)
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.references.service import OWNED_REFERENCE_PREFIX
from companion_v2.references.wardrobe import (
    GROUP as WARDROBE_GROUP,
    WardrobeConfigurationOwner,
    configuration_fields as wardrobe_fields,
)
from companion_v2.storage.runtime import StorageRuntime


MANAGEMENT_SCOPE = "page.management"
MANAGEMENT_ACTION = "memory.manage"
ALBUM_LIMIT = 16
ALBUM_SELECTION_LIMIT = 24
ALBUM_EXPORT_BYTES = 32 * 1024 * 1024
ALBUM_HIDDEN_KEY_PREFIX = "image.album.hidden.v1:"
THUMBNAIL_BYTES = 160 * 1024
REFERENCE_THUMBNAIL_BYTES = 64 * 1024
UPLOAD_MAX_BYTES = 12 * 1024 * 1024
IMAGE_ROLES = tuple(role.value for role in ReferenceRole)


def _asset_view(repository, owner, asset):
    return {
        "asset_id": asset.asset_id,
        "label": asset.label,
        "roles": repository.roles_of(owner, asset.asset_id),
        "usage": repository.usage_of(owner, asset.asset_id),
        "state": asset.state.value,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "revision": asset.revision,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


class ReferenceMaterialManagement:
    """Control-plane facade; its caller must supply the host-pinned role context."""

    def __init__(self, application):
        self.application = application
        self.runtime: StorageRuntime = application.runtime
        self.repository = ReferenceAssetRepository(self.runtime)

    def _admit(self, operator, context, at):
        if not isinstance(context, TaskContext) or context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("reference_scope_invalid", "image materials are managed for a configured private role")
        if context.audience.recipients != (context.actor.subject,):
            raise TaskError("reference_context_invalid", "the selected role context is not a private audience")
        scope = ScopeRef(self.runtime.installation_id, "companion", ScopeKind.SYSTEM, MANAGEMENT_SCOPE)
        require(ObjectAccessPolicy(self.runtime).feature_access(
            operator, scope=scope, action=MANAGEMENT_ACTION, observed_at=at))
        current = require(PersistentIdentityDirectory(self.runtime).resolve_authenticated(
            context.actor.principal, observed_at=at))
        if (current.subject, current.binding_id, current.binding_revision) != (
                context.actor.subject, context.actor.binding_id, context.actor.binding_revision):
            raise TaskError("binding_rebound", "the selected private identity has changed")
        directory = PersonaDirectory(self.runtime)
        route = directory.route(context.scope)
        binding = require(PersistentIdentityDirectory(self.runtime).resolve_role(
            context.scope, observed_at=at))
        if (route is None or route.scene_kind is not ScopeKind.PRIVATE
                or route.user != context.actor.subject or binding != context.role_binding):
            raise TaskError("reference_target_changed", "the configured role or private target has changed")
        return self.application.references.owner(context)

    def _settings(self, context):
        references = ReferenceConfigurationOwner(self.runtime).read(
            self.application.reference_settings.target(context), reference_fields())
        wardrobe = WardrobeConfigurationOwner(self.runtime).read(
            self.application.wardrobe_settings.target(context), wardrobe_fields())
        return (
            {key.removeprefix(REFERENCE_GROUP + "."): item.value for key, item in references.fields.items()},
            {key.removeprefix(WARDROBE_GROUP + "."): item.value for key, item in wardrobe.fields.items()},
        )

    @staticmethod
    def _local_date(application, context, at):
        timezone_name = application.messages.timezone_name
        try:
            snapshot = application.perception_settings.capture(context, at)
            timezone_name = snapshot["settings"]["calendar_timezone"]
        except (TaskError, KeyError, TypeError, ValueError):
            pass
        try:
            return at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            return at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()

    def inventory(self, operator, context, *, at):
        owner = self._admit(operator, context, at)
        reference_options, wardrobe_options = self._settings(context)
        assets = (*self.repository.list(owner, state=ReferenceState.ACTIVE),
                  *self.repository.list(owner, state=ReferenceState.WITHDRAWN))
        items = self.repository.list_items(owner, state=None)
        reference_views = []
        for asset in assets:
            view = _asset_view(self.repository, owner, asset)
            if asset.state is ReferenceState.ACTIVE:
                payload = self.repository.get_payload(owner, asset.asset_id)
                if payload is not None:
                    try:
                        view["thumbnail"] = self._thumbnail(payload, byte_limit=REFERENCE_THUMBNAIL_BYTES)
                    except TaskError as error:
                        if error.code != "reference_thumbnail_invalid":
                            raise
                        view["thumbnail"] = ""
                    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
                        view["thumbnail"] = ""
            reference_views.append(view)
        local_date = self._local_date(self.application, context, at)
        selected = self.repository.daily_outfit_item(owner, local_date)
        daily = None if selected is None else {
            "local_date": local_date, "item_id": selected.item_id, "name": selected.name,
            "asset_id": selected.asset_id, "revision": selected.revision,
        }
        return {
            "settings": {"references": reference_options, "wardrobe": wardrobe_options},
            "scene_presets": parse_scene_presets(image_settings(self.runtime, context)["scene_presets_json"]),
            "references": reference_views,
            "wardrobe": [{"item_id": item.item_id, "name": item.name,
                          "description": item.description, "tags": item.tags,
                          "asset_id": item.asset_id, "state": item.state.value,
                          "revision": item.revision} for item in items],
            "daily_outfit": daily,
        }

    @staticmethod
    def _thumbnail(payload, *, byte_limit):
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as source:
                source.load()
                preview = ImageOps.exif_transpose(source)
                preview.thumbnail((256, 256), Image.Resampling.LANCZOS)
                rgba = preview.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, "#f3f4f4")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                thumbnail = BytesIO()
                canvas.save(thumbnail, format="JPEG", quality=68, optimize=True)
        data = thumbnail.getvalue()
        if not data or len(data) > byte_limit:
            raise TaskError("reference_thumbnail_invalid", "a reference preview exceeds its image budget")
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")

    @staticmethod
    def _validate_upload(payload, declared_mime):
        expected_format = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}.get(declared_mime)
        if not expected_format:
            raise TaskError("reference_image_type_invalid", "reference images support PNG, JPEG and WebP")
        if not isinstance(payload, bytes) or not payload or len(payload) > UPLOAD_MAX_BYTES:
            raise TaskError("reference_upload_too_large", "reference images must be 12 MiB or smaller")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as source:
                    if source.format != expected_format:
                        raise TaskError("reference_image_type_mismatch", "the declared image type does not match its contents")
                    if getattr(source, "n_frames", 1) != 1:
                        raise TaskError("reference_image_animated", "animated images cannot be registered as reference material")
                    source.verify()
                with Image.open(BytesIO(payload)) as source:
                    if source.format != expected_format:
                        raise TaskError("reference_image_type_mismatch", "the declared image type does not match its contents")
                    source.load()
        except TaskError:
            raise
        except Exception as error:
            raise TaskError("reference_image_invalid", "the image is damaged or cannot be decoded") from error
        return declared_mime

    def register_uploaded_reference(self, operator, context, *, at, payload, mime_type,
                                    label, roles, operation_key):
        owner = self._admit(operator, context, at)
        reference_options, _wardrobe_options = self._settings(context)
        if not reference_options["enabled"]:
            raise TaskError("photo_reference_disabled", "turn on reference images for this role before registering one")
        if type(label) is not str or label != label.strip() or len(label) > 120:
            raise TaskError("reference_label_invalid", "the image label must be trimmed and under 120 characters")
        operation_key = self._operation_key(operation_key)
        selected_roles = self._roles(roles)
        mime_type = self._validate_upload(payload, mime_type)
        identifier = self._asset_id(owner, operation_key)
        asset = ReferenceAsset(
            asset_id=identifier, kind=ReferenceKind.REFERENCE_IMAGE, role=selected_roles[0],
            content_hash=ContentDigest(sha256(payload).hexdigest()), size_bytes=len(payload),
            mime_type=mime_type, media_ref=OWNED_REFERENCE_PREFIX + "upload:" + identifier,
            source=ReferenceSource.OPERATOR, label=label)
        existing = self.repository.get(owner, identifier)
        expected_roles = tuple(role.value for role in selected_roles)
        if existing is not None:
            if (any(getattr(existing, field) != getattr(asset, field) for field in (
                        "asset_id", "kind", "role", "content_hash", "size_bytes", "mime_type",
                        "media_ref", "source", "label"))
                    or existing.state is not ReferenceState.ACTIVE
                    or self.repository.roles_of(owner, identifier) != expected_roles
                    or self.repository.get_payload(owner, identifier) != payload):
                raise TaskError("reference_operation_conflict", "this upload key already names different material")
            return _asset_view(self.repository, owner, existing)
        with self.runtime.transaction():
            stored = self.repository.put(owner, asset, created_at=at, payload=payload)
            for role in selected_roles[1:]:
                self.repository.add_role(owner, identifier, role, created_at=at)
        return _asset_view(self.repository, owner, stored)

    def update_reference(self, operator, context, asset_id, *, at, label, roles, expected_revision, usage=None):
        owner = self._admit(operator, context, at)
        if type(asset_id) is not str or not asset_id:
            raise TaskError("reference_asset_missing", "select one registered reference image")
        if type(label) is not str or label != label.strip() or len(label) > 120:
            raise TaskError("reference_label_invalid", "the image label must be trimmed and under 120 characters")
        if type(expected_revision) is not int or expected_revision < 1:
            raise TaskError("reference_revision_invalid", "reload the reference material before editing")
        selected_roles = self._roles(roles)
        asset = self.repository.update_registration(
            owner, asset_id, label=label, roles=selected_roles,
            expected_revision=expected_revision, updated_at=at, usage=usage)
        return _asset_view(self.repository, owner, asset)

    def reorder_references(self, operator, context, *, expected_asset_ids, asset_ids, at):
        owner = self._admit(operator, context, at)
        for value in (expected_asset_ids, asset_ids):
            if (type(value) is not list or len(value) > 24
                    or any(type(item) is not str or not item or len(item) > 128 for item in value)
                    or len(set(value)) != len(value)):
                raise TaskError("reference_order_invalid", "the gallery order must contain unique reference IDs")
        ordered = self.repository.reorder_active(
            owner, expected_asset_ids=expected_asset_ids, asset_ids=asset_ids)
        return {"asset_ids": list(ordered)}

    @staticmethod
    def _album_selection(image_ids):
        if (type(image_ids) is not list or not 1 <= len(image_ids) <= ALBUM_SELECTION_LIMIT
                or any(type(item) is not str or not item or len(item) > 180 for item in image_ids)
                or len(set(image_ids)) != len(image_ids)):
            raise TaskError("generated_album_selection_invalid", "select between 1 and 24 unique album images")
        return list(image_ids)

    def _hidden_album_ids(self, context):
        key = ALBUM_HIDDEN_KEY_PREFIX + context.key
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (key,))
        if row is None:
            return set()
        try:
            values = json.loads(row["value"])
        except (TypeError, ValueError):
            return set()
        return {item for item in values if type(item) is str and item} if isinstance(values, list) else set()

    def _hide_album_ids(self, context, image_ids):
        key = ALBUM_HIDDEN_KEY_PREFIX + context.key
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key=?", (key,)).fetchone()
            try:
                hidden = set(json.loads(row["value"])) if row is not None else set()
            except (TypeError, ValueError):
                hidden = set()
            hidden.update(image_ids)
            payload = json.dumps(sorted(hidden), ensure_ascii=True, separators=(",", ":"))
            connection.execute(
                "INSERT INTO runtime_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, payload))
        return hidden

    def export_album(self, operator, context, *, at, image_ids):
        identifiers = self._album_selection(image_ids)
        self._admit(operator, context, at)
        archive = BytesIO()
        total = 0
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for index, image_id in enumerate(identifiers, 1):
                item = self.application.messages.generated_images.album_image(context, at, image_id)
                total += len(item.content)
                if total > ALBUM_EXPORT_BYTES:
                    raise TaskError("generated_album_export_too_large",
                                    "the selected album export exceeds the size limit")
                extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                    getattr(item.descriptor, "mime_type", ""), "bin")
                stem = str(item.created_at).replace(":", "").replace("-", "").replace("+", "")[:18]
                bundle.writestr(f"album-{index:02d}-{stem}.{extension}", item.content)
        data = archive.getvalue()
        return {"filename": "companion-album-" + at.strftime("%Y%m%d-%H%M%S") + ".zip",
                "mime_type": "application/zip",
                "data_url": "data:application/zip;base64," + base64.b64encode(data).decode("ascii"),
                "count": len(identifiers)}

    def hide_album_images(self, operator, context, *, at, image_ids):
        identifiers = self._album_selection(image_ids)
        self._admit(operator, context, at)
        for image_id in identifiers:
            # Hiding is a non-destructive gallery projection.  A stale native
            # item may no longer be readable (expired source, changed batch),
            # but the operator must still be able to remove its card.  Do not
            # let one unreadable image abort the whole selection.
            try:
                self.application.messages.generated_images.album_image(context, at, image_id)
            except TaskError:
                pass
        hidden = self._hide_album_ids(context, identifiers)
        return {"hidden": len(identifiers), "hidden_total": len(hidden)}

    def open_album_folder(self, operator, context, *, at):
        self._admit(operator, context, at)
        from pathlib import Path
        root = Path(self.runtime.data_root).resolve()
        if not root.is_dir():
            raise TaskError("generated_album_folder_unavailable", "the V2 data folder is unavailable")
        try:
            if os.name == "nt":
                os.startfile(str(root))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(root)])
            else:
                subprocess.Popen(["xdg-open", str(root)])
        except (OSError, AttributeError, ValueError) as error:
            raise TaskError("generated_album_open_failed", "the V2 data folder could not be opened") from error
        return {"path": str(root), "opened": True}

    def album(self, operator, context, *, at, cursor=None):
        self._admit(operator, context, at)
        page = self.application.messages.generated_images.album_page(
            context, at, limit=ALBUM_LIMIT, cursor=cursor)
        hidden = self._hidden_album_ids(context)
        items = []
        for item in page["items"]:
            if item.image_id in hidden:
                continue
            with Image.open(BytesIO(item.content)) as source:
                source.load()
                source.thumbnail((480, 480), Image.Resampling.LANCZOS)
                thumbnail = BytesIO()
                source.convert("RGB").save(thumbnail, format="JPEG", quality=76, optimize=True)
            data = thumbnail.getvalue()
            if not data or len(data) > THUMBNAIL_BYTES:
                raise TaskError("generated_album_thumbnail_invalid", "an album preview exceeds its image budget")
            items.append({
                "image_id": item.image_id,
                "created_at": item.created_at,
                "action": item.action,
                "width": item.descriptor.width,
                "height": item.descriptor.height,
                "thumbnail": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
            })
        return {"items": items, "next_cursor": page["next_cursor"]}

    @staticmethod
    def _operation_key(value):
        if (type(value) is not str or not 16 <= len(value) <= 128
                or value != value.strip() or any(ord(char) < 33 for char in value)):
            raise TaskError("reference_operation_invalid", "a bounded operation key is required")
        return value

    @staticmethod
    def _roles(values):
        if (type(values) is not list or not 1 <= len(values) <= len(IMAGE_ROLES)
                or any(type(value) is not str or value not in IMAGE_ROLES for value in values)
                or len(set(values)) != len(values)):
            raise TaskError("reference_roles_invalid", "choose one or more supported image uses")
        return tuple(sorted((ReferenceRole(value) for value in values), key=lambda role: role.value))

    @staticmethod
    def _asset_id(owner, operation_key):
        return "ref_" + digest(["role-reference-registration.v1", owner, operation_key])[:48]

    def register_from_album(self, operator, context, *, at, image_id, label, roles, operation_key):
        owner = self._admit(operator, context, at)
        reference_options, _wardrobe_options = self._settings(context)
        if not reference_options["enabled"]:
            raise TaskError("photo_reference_disabled", "turn on reference images for this role before registering one")
        if type(image_id) is not str or not 1 <= len(image_id) <= 180:
            raise TaskError("generated_image_missing", "select one image from this role's album")
        if type(label) is not str or label != label.strip() or len(label) > 120:
            raise TaskError("reference_label_invalid", "the image label must be trimmed and under 120 characters")
        operation_key = self._operation_key(operation_key)
        selected_roles = self._roles(roles)
        original = self.application.messages.generated_images.album_image(context, at, image_id)
        payload = original.content
        if len(payload) > MAX_ASSET_BYTES:
            raise TaskError("reference_asset_too_large", "this image exceeds the 20 MiB reference limit")
        descriptor = original.descriptor
        if type(descriptor) is not ImageDescriptor:
            raise TaskError("reference_image_animated", "animated images cannot be registered as reference material")
        identifier = self._asset_id(owner, operation_key)
        media_ref = OWNED_REFERENCE_PREFIX + "album:" + sha256(image_id.encode("utf-8")).hexdigest() + ":" + identifier
        asset = ReferenceAsset(
            asset_id=identifier, kind=ReferenceKind.REFERENCE_IMAGE, role=selected_roles[0],
            content_hash=ContentDigest(descriptor.content_hash), size_bytes=len(payload),
            mime_type=descriptor.mime_type, media_ref=media_ref,
            source=ReferenceSource.OPERATOR, label=label)
        existing = self.repository.get(owner, identifier)
        expected_roles = tuple(role.value for role in selected_roles)
        if existing is not None:
            if (any(getattr(existing, field) != getattr(asset, field) for field in (
                        "asset_id", "kind", "role", "content_hash", "size_bytes", "mime_type",
                        "media_ref", "source", "label"))
                    or existing.state is not ReferenceState.ACTIVE
                    or self.repository.roles_of(owner, identifier) != expected_roles
                    or self.repository.get_payload(owner, identifier) != payload):
                raise TaskError("reference_operation_conflict", "this registration key already names different material")
            return _asset_view(self.repository, owner, existing)
        with self.runtime.transaction():
            stored = self.repository.put(owner, asset, created_at=at, payload=payload)
            for role in selected_roles[1:]:
                self.repository.add_role(owner, identifier, role, created_at=at)
        return _asset_view(self.repository, owner, stored)

    def withdraw_reference(self, operator, context, asset_id, *, at, expected_revision=None):
        owner = self._admit(operator, context, at)
        if type(asset_id) is not str or not asset_id:
            raise TaskError("reference_asset_missing", "select one registered reference image")
        return {"withdrawn": bool(self.repository.withdraw(
            owner, asset_id, updated_at=at, expected_revision=expected_revision))}

    def register_wardrobe_item(self, operator, context, *, at, operation_key, name,
                               description="", tags=(), asset_id=None):
        owner = self._admit(operator, context, at)
        _reference_options, wardrobe_options = self._settings(context)
        if not wardrobe_options["enabled"]:
            raise TaskError("wardrobe_disabled", "turn on the wardrobe before adding entries")
        operation_key = self._operation_key(operation_key)
        if type(tags) is not list or any(type(tag) is not str for tag in tags):
            raise TaskError("wardrobe_tags_invalid", "wardrobe tags must be a list of text labels")
        if asset_id is not None:
            linked = self.repository.get(owner, asset_id) if type(asset_id) is str else None
            if (linked is None or linked.state is not ReferenceState.ACTIVE
                    or ReferenceRole.OUTFIT.value not in self.repository.roles_of(owner, asset_id)):
                raise TaskError("wardrobe_reference_invalid", "a wardrobe picture must be an active outfit reference")
        identifier = "wardrobe_" + digest(["role-wardrobe-registration.v1", owner, operation_key])[:48]
        item = WardrobeItem(item_id=identifier, name=name, description=description,
                            tags=tuple(sorted(tags)), asset_id=asset_id,
                            source=ReferenceSource.OPERATOR)
        existing = self.repository.get_item(owner, identifier)
        if existing is not None:
            if any(getattr(existing, field) != getattr(item, field) for field in (
                    "item_id", "name", "description", "tags", "asset_id", "source")):
                raise TaskError("wardrobe_operation_conflict", "this wardrobe operation key already names another item")
            return {"item_id": existing.item_id, "name": existing.name, "asset_id": existing.asset_id,
                    "state": existing.state.value, "revision": existing.revision}
        stored = self.repository.put_item(owner, item, created_at=at)
        return {"item_id": stored.item_id, "name": stored.name, "asset_id": stored.asset_id,
                "state": stored.state.value, "revision": stored.revision}

    def withdraw_wardrobe_item(self, operator, context, item_id, *, at):
        owner = self._admit(operator, context, at)
        if type(item_id) is not str or not item_id:
            raise TaskError("wardrobe_item_missing", "select one wardrobe item")
        return {"withdrawn": bool(self.repository.withdraw_item(owner, item_id, updated_at=at))}

    def select_daily_outfit(self, operator, context, item_id, *, at):
        owner = self._admit(operator, context, at)
        reference_options, wardrobe_options = self._settings(context)
        if not reference_options["enabled"]:
            raise TaskError("photo_reference_disabled", "turn on reference images before choosing a daily outfit")
        if not reference_options["daily_outfit_enabled"]:
            raise TaskError("daily_outfit_disabled", "turn on daily outfit selection for this role")
        if not wardrobe_options["enabled"]:
            raise TaskError("wardrobe_disabled", "turn on the wardrobe before choosing a daily outfit")
        if type(item_id) is not str or not item_id:
            raise TaskError("wardrobe_item_missing", "select one active wardrobe item")
        local_date = self._local_date(self.application, context, at)
        selected = self.repository.select_daily_item(owner, local_date, item_id, created_at=at)
        return {"local_date": local_date, "item_id": selected.item_id, "name": selected.name,
                "asset_id": selected.asset_id, "revision": selected.revision}
