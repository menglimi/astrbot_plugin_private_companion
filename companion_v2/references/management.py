"""Dashboard management for role-owned image references and wardrobe records."""

from __future__ import annotations

import base64
from collections import OrderedDict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Lock
import warnings
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageOps

from companion_v2.conversation.media.models import MAX_IMAGE_BYTES, ImageDescriptor
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
from companion_v2.references.daily_outfit import (
    DAILY_OUTFIT_ACTION,
    DAILY_OUTFIT_IMAGE_PREFIX,
    list_daily_outfit_records,
    load_daily_outfit_record,
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
from companion_v2.storage.generated_album import (
    ALBUM_HIDDEN_KEY_PREFIX, album_filename, hidden_album_ids, sync_generated_album_folder,
)
from companion_v2.storage.runtime import StorageRuntime


MANAGEMENT_SCOPE = "page.management"
MANAGEMENT_ACTION = "memory.manage"
ALBUM_LIMIT = 24
ALBUM_MAX_LIMIT = 60
ALBUM_SYNC_PAGE_LIMIT = 60
ALBUM_SELECTION_LIMIT = 24
ALBUM_EXPORT_BYTES = 32 * 1024 * 1024
ALBUM_THUMBNAIL_SIZE = 360
ALBUM_THUMBNAIL_QUALITY = 74
ALBUM_THUMBNAIL_MEMORY_CAP = 512
ALBUM_THUMBNAIL_DISK_CAP = 1024
ALBUM_THUMBNAIL_DIRNAME = "album-thumbnails"
THUMBNAIL_BYTES = 160 * 1024
REFERENCE_THUMBNAIL_BYTES = 64 * 1024
THUMBNAIL_CACHE_MAX_ENTRIES = 256
UPLOAD_MAX_BYTES = 12 * 1024 * 1024
IMAGE_ROLES = tuple(role.value for role in ReferenceRole)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_MERGED_CURSOR_PREFIX = "v2m:"
_ALBUM_THUMBNAIL_LOCK = Lock()
_ALBUM_THUMBNAIL_MEMORY: OrderedDict[tuple[str, int], bytes] = OrderedDict()


class _DailyOutfitDescriptor:
    __slots__ = ("position", "mime_type", "width", "height", "byte_count", "content_hash")

    def __init__(self, *, mime_type: str, width: int, height: int, byte_count: int, content_hash: str):
        self.position = 0
        self.mime_type = mime_type
        self.width = width
        self.height = height
        self.byte_count = byte_count
        self.content_hash = content_hash


class _DailyOutfitAlbumItem:
    __slots__ = ("image_id", "created_at", "action", "descriptor", "content",
                 "local_date", "weather", "activities")

    def __init__(self, *, image_id: str, created_at: str, action: str, descriptor, content: bytes,
                 local_date: str = "", weather=None, activities=()):
        self.image_id = image_id
        self.created_at = created_at
        self.action = action
        self.descriptor = descriptor
        self.content = content
        self.local_date = local_date
        self.weather = weather
        self.activities = list(activities or ())


def _build_daily_outfit_album_item(loaded: dict) -> _DailyOutfitAlbumItem:
    content = loaded["content"]
    mime_type = loaded["mime_type"]
    width = loaded["width"]
    height = loaded["height"]
    content_hash = loaded["content_hash"]
    byte_count = len(content)
    descriptor = None
    if (mime_type in {"image/png", "image/jpeg", "image/webp"}
            and 1 <= width <= 8192 and 1 <= height <= 8192
            and 0 < byte_count <= MAX_IMAGE_BYTES):
        try:
            descriptor = ImageDescriptor(
                position=0, mime_type=mime_type, width=width, height=height,
                byte_count=byte_count, content_hash=content_hash)
        except TaskError:
            descriptor = None
    if descriptor is None:
        descriptor = _DailyOutfitDescriptor(
            mime_type=mime_type, width=width, height=height,
            byte_count=byte_count, content_hash=content_hash)
    return _DailyOutfitAlbumItem(
        image_id=loaded["image_id"],
        created_at=loaded["created_at"],
        action=loaded.get("action") or DAILY_OUTFIT_ACTION,
        descriptor=descriptor,
        content=content,
        local_date=loaded.get("local_date") or "",
        weather=loaded.get("weather"),
        activities=loaded.get("activities") or (),
    )


def _encode_merged_album_cursor(gen_cursor, gen_done: bool, outfit_offset: int,
                                gen_skip: int, gen_limit: int) -> str:
    payload = json.dumps(
        {"gc": gen_cursor, "gd": bool(gen_done), "o": int(outfit_offset),
         "gs": int(gen_skip), "gl": int(gen_limit)},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    encoded = _MERGED_CURSOR_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(encoded) > 1024:
        raise TaskError("generated_album_cursor_invalid", "the album cursor is invalid")
    return encoded


def _decode_merged_album_cursor(cursor):
    if cursor is None:
        return None, False, 0, 0, None, False
    if type(cursor) is not str or not 8 <= len(cursor) <= 1024:
        raise TaskError("generated_album_cursor_invalid", "the album cursor is invalid")
    if not cursor.startswith(_MERGED_CURSOR_PREFIX):
        return cursor, False, 0, 0, None, False
    raw = cursor[len(_MERGED_CURSOR_PREFIX):]
    try:
        padding = "=" * (-len(raw) % 4)
        doc = json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))
        if type(doc) is not dict or set(doc) != {"gc", "gd", "o", "gs", "gl"}:
            raise ValueError("unexpected merged cursor keys")
        gc = doc["gc"]
        if gc is not None and (type(gc) is not str or not 8 <= len(gc) <= 1024):
            raise ValueError("invalid generated cursor")
        if (type(doc["gd"]) is not bool
                or type(doc["o"]) is not int or isinstance(doc["o"], bool) or doc["o"] < 0
                or type(doc["gs"]) is not int or isinstance(doc["gs"], bool) or doc["gs"] < 0
                or type(doc["gl"]) is not int or isinstance(doc["gl"], bool)
                or not 1 <= doc["gl"] <= ALBUM_MAX_LIMIT):
            raise ValueError("invalid merged cursor values")
        return gc, doc["gd"], doc["o"], doc["gs"], doc["gl"], True
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise TaskError("generated_album_cursor_invalid", "the album cursor is invalid") from None


def _thumbnail_cache_dir(data_root) -> Path | None:
    if data_root is None:
        return None
    try:
        root = Path(data_root).resolve()
        if not root.is_dir():
            return None
        media_dir = root / "media"
        if media_dir.is_symlink():
            return None
        cache_dir = media_dir / ALBUM_THUMBNAIL_DIRNAME
        if cache_dir.is_symlink():
            return None
        resolved = cache_dir.resolve()
        if not resolved.is_relative_to(root):
            return None
        return resolved
    except (OSError, ValueError):
        return None


def _prune_thumbnail_disk_cache(cache_dir: Path, *, cap: int = ALBUM_THUMBNAIL_DISK_CAP) -> None:
    try:
        entries = []
        with os.scandir(cache_dir) as scan:
            for entry in scan:
                if entry.name.startswith(".") or not entry.name.endswith(".jpg"):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    entries.append((entry.stat(follow_symlinks=False).st_mtime, Path(entry.path)))
                except OSError:
                    continue
        if len(entries) <= cap:
            return
        target_keep = max(1, int(cap * 0.8))
        entries.sort(key=lambda pair: pair[0])
        for _, victim in entries[:len(entries) - target_keep]:
            try:
                victim.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _read_disk_thumbnail(cache_dir: Path | None, content_hash: str, size: int) -> bytes | None:
    if cache_dir is None or _HEX64_RE.fullmatch(content_hash) is None:
        return None
    target = cache_dir / f"{content_hash}-{size}.jpg"
    try:
        if target.is_symlink() or not target.is_file():
            return None
        if target.resolve().parent != cache_dir:
            return None
        data = target.read_bytes()
        if 0 < len(data) <= THUMBNAIL_BYTES and data.startswith(b"\xff\xd8\xff"):
            return data
    except OSError:
        return None
    return None


def _write_disk_thumbnail(cache_dir: Path | None, content_hash: str, size: int, data: bytes) -> None:
    if cache_dir is None or _HEX64_RE.fullmatch(content_hash) is None or not data:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        resolved_dir = cache_dir.resolve()
        target = resolved_dir / f"{content_hash}-{size}.jpg"
        if target.parent != resolved_dir or target.is_symlink() or target.exists():
            return
        handle, temporary = tempfile.mkstemp(prefix=".thumb-", suffix=".tmp", dir=resolved_dir)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            if not target.exists() and not target.is_symlink():
                os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        _prune_thumbnail_disk_cache(resolved_dir)
    except OSError:
        pass


def _clear_album_thumbnail_memory_cache() -> None:
    with _ALBUM_THUMBNAIL_LOCK:
        _ALBUM_THUMBNAIL_MEMORY.clear()


_THUMBNAIL_CACHE_LOCK = Lock()
_THUMBNAIL_CACHE: OrderedDict[tuple[bytes, int, int, int, str | None], str] = OrderedDict()


def _cached_thumbnail(
    payload: bytes,
    *,
    size: int,
    quality: int,
    byte_limit: int,
    alpha_bg: str | None,
    error_code: str,
    error_message: str,
) -> str:
    """Encode and LRU-cache a JPEG data-URL thumbnail keyed by content SHA-256."""
    cache_key = (sha256(payload).digest(), size, quality, byte_limit, alpha_bg)
    with _THUMBNAIL_CACHE_LOCK:
        cached = _THUMBNAIL_CACHE.get(cache_key)
        if cached is not None:
            _THUMBNAIL_CACHE.move_to_end(cache_key)
            return cached
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(payload)) as source:
            source.load()
            if alpha_bg is not None:
                preview = ImageOps.exif_transpose(source)
                preview.thumbnail((size, size), Image.Resampling.LANCZOS)
                rgba = preview.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, alpha_bg)
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                thumbnail = BytesIO()
                canvas.save(thumbnail, format="JPEG", quality=quality, optimize=True)
            else:
                source.thumbnail((size, size), Image.Resampling.LANCZOS)
                thumbnail = BytesIO()
                source.convert("RGB").save(thumbnail, format="JPEG", quality=quality, optimize=True)
    data = thumbnail.getvalue()
    if not data or len(data) > byte_limit:
        raise TaskError(error_code, error_message)
    encoded = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    with _THUMBNAIL_CACHE_LOCK:
        _THUMBNAIL_CACHE[cache_key] = encoded
        _THUMBNAIL_CACHE.move_to_end(cache_key)
        while len(_THUMBNAIL_CACHE) > THUMBNAIL_CACHE_MAX_ENTRIES:
            _THUMBNAIL_CACHE.popitem(last=False)
    return encoded


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
        active_items = self.repository.list_items(owner, state=ReferenceState.ACTIVE)
        withdrawn_items = self.repository.list_items(owner, state=ReferenceState.WITHDRAWN, order_by_updated=True)
        items = (*active_items, *withdrawn_items)
        withdrawn_total = self.repository.count_items(owner, state=ReferenceState.WITHDRAWN)
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
            "wardrobe_counts": {"active": len(active_items), "withdrawn": withdrawn_total},
            "daily_outfit": daily,
        }

    @staticmethod
    def _thumbnail(payload, *, byte_limit, size=256):
        return _cached_thumbnail(
            payload,
            size=size,
            quality=68,
            byte_limit=byte_limit,
            alpha_bg="#f3f4f4",
            error_code="reference_thumbnail_invalid",
            error_message="a reference preview exceeds its image budget",
        )

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
        return hidden_album_ids(self.runtime, context)

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

    def _resolve_album_item(self, owner, context, at, image_id):
        if isinstance(image_id, str) and image_id.startswith(DAILY_OUTFIT_IMAGE_PREFIX):
            loaded = load_daily_outfit_record(self.runtime, owner, image_id)
            if loaded is None:
                raise TaskError("generated_image_missing", "the selected album image is unavailable")
            return _build_daily_outfit_album_item(loaded)
        return self.application.messages.generated_images.album_image(context, at, image_id)

    def export_album(self, operator, context, *, at, image_ids):
        identifiers = self._album_selection(image_ids)
        owner = self._admit(operator, context, at)
        archive = BytesIO()
        total = 0
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for index, image_id in enumerate(identifiers, 1):
                item = self._resolve_album_item(owner, context, at, image_id)
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
        owner = self._admit(operator, context, at)
        for image_id in identifiers:
            # Hiding is a non-destructive gallery projection.  A stale native
            # item may no longer be readable (expired source, changed batch),
            # but the operator must still be able to remove its card.  Do not
            # let one unreadable image abort the whole selection.
            try:
                self._resolve_album_item(owner, context, at, image_id)
            except TaskError:
                pass
        hidden = self._hide_album_ids(context, identifiers)
        return {"hidden": len(identifiers), "hidden_total": len(hidden)}

    def _iter_album_items(self, context, at, *, owner=None):
        hidden = self._hidden_album_ids(context)
        cursor = None
        seen_cursors = set()
        seen_ids = set()
        while True:
            page = self.application.messages.generated_images.album_page(
                context, at, limit=ALBUM_SYNC_PAGE_LIMIT, cursor=cursor)
            for item in page.get("items") or ():
                image_id = getattr(item, "image_id", None)
                if not image_id or image_id in hidden or image_id in seen_ids:
                    continue
                seen_ids.add(image_id)
                yield item
            next_cursor = page.get("next_cursor")
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if owner:
            for record in list_daily_outfit_records(self.runtime, owner, hidden_ids=hidden):
                image_id = record["image_id"]
                if image_id in seen_ids:
                    continue
                loaded = load_daily_outfit_record(self.runtime, owner, image_id)
                if loaded is None:
                    continue
                seen_ids.add(image_id)
                yield _build_daily_outfit_album_item(loaded)

    def open_album_folder(self, operator, context, *, at, role_id=None):
        owner = self._admit(operator, context, at)
        from pathlib import Path
        root = Path(self.runtime.data_root).resolve()
        if not root.is_dir():
            raise TaskError("generated_album_folder_unavailable", "the generated album folder is unavailable")
        bound_role = getattr(getattr(getattr(context, "role_binding", None), "role", None), "subject_id", None)
        if role_id is not None:
            if type(role_id) is not str:
                raise TaskError("generated_album_folder_unavailable", "the requested role identifier is invalid")
            if not role_id.strip():
                effective_role_id = None
            elif role_id != bound_role:
                raise TaskError("reference_target_changed", "the requested role does not match the active role context")
            else:
                effective_role_id = role_id
        else:
            effective_role_id = bound_role if isinstance(bound_role, str) and bound_role.strip() else None
        try:
            entries = self._iter_album_items(context, at, owner=owner) if effective_role_id is not None else ()
            synced = sync_generated_album_folder(root, entries, role_id=effective_role_id, owner=owner)
        except (OSError, ValueError) as error:
            raise TaskError("generated_album_folder_unavailable", "the generated album folder could not be prepared") from error
        target = Path(synced["path"])
        if not target.is_dir():
            raise TaskError("generated_album_folder_unavailable", "the generated album folder is unavailable")
        try:
            if os.name == "nt":
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except (OSError, AttributeError, ValueError) as error:
            raise TaskError("generated_album_open_failed", "the generated album folder could not be opened") from error
        return {
            "path": synced["path"],
            "opened": True,
            "synced_count": synced["synced_count"],
            "total_count": synced["total_count"],
        }

    @staticmethod
    def _resolve_album_mime_type(content: bytes, declared: str = "") -> str:
        normalized = (declared or "").strip().lower()
        if normalized in _SAFE_IMAGE_MIMES:
            return normalized
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if content[:6] in {b"GIF87a", b"GIF89a"}:
            return "image/gif"
        return "application/octet-stream"

    @staticmethod
    def _render_album_thumbnail(payload: bytes, *, size: int = ALBUM_THUMBNAIL_SIZE) -> bytes:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as source:
                source.load()
                preview = ImageOps.exif_transpose(source)
                preview.thumbnail((size, size), Image.Resampling.LANCZOS)
                if preview.mode in ("RGBA", "LA") or (preview.mode == "P" and "transparency" in preview.info):
                    rgba = preview.convert("RGBA")
                    canvas = Image.new("RGB", rgba.size, "#f3f4f4")
                    canvas.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    canvas = preview.convert("RGB")
                thumbnail = BytesIO()
                canvas.save(thumbnail, format="JPEG", quality=ALBUM_THUMBNAIL_QUALITY, optimize=True)
        data = thumbnail.getvalue()
        if not data or len(data) > THUMBNAIL_BYTES:
            raise TaskError("generated_album_thumbnail_invalid", "an album preview exceeds its image budget")
        return data

    def _album_thumbnail_bytes(self, item) -> bytes:
        raw_hash = getattr(getattr(item, "descriptor", None), "content_hash", "")
        if isinstance(raw_hash, str) and _HEX64_RE.fullmatch(raw_hash.lower()):
            content_hash = raw_hash.lower()
        else:
            content_hash = sha256(item.content).hexdigest()
        cache_key = (content_hash, ALBUM_THUMBNAIL_SIZE)
        with _ALBUM_THUMBNAIL_LOCK:
            cached = _ALBUM_THUMBNAIL_MEMORY.get(cache_key)
            if cached is not None:
                _ALBUM_THUMBNAIL_MEMORY.move_to_end(cache_key)
                return cached
        cache_dir = _thumbnail_cache_dir(getattr(self.runtime, "data_root", None))
        disk_hit = _read_disk_thumbnail(cache_dir, content_hash, ALBUM_THUMBNAIL_SIZE)
        if disk_hit is not None:
            with _ALBUM_THUMBNAIL_LOCK:
                _ALBUM_THUMBNAIL_MEMORY[cache_key] = disk_hit
                _ALBUM_THUMBNAIL_MEMORY.move_to_end(cache_key)
                while len(_ALBUM_THUMBNAIL_MEMORY) > ALBUM_THUMBNAIL_MEMORY_CAP:
                    _ALBUM_THUMBNAIL_MEMORY.popitem(last=False)
            return disk_hit
        data = self._render_album_thumbnail(item.content, size=ALBUM_THUMBNAIL_SIZE)
        _write_disk_thumbnail(cache_dir, content_hash, ALBUM_THUMBNAIL_SIZE, data)
        with _ALBUM_THUMBNAIL_LOCK:
            _ALBUM_THUMBNAIL_MEMORY[cache_key] = data
            _ALBUM_THUMBNAIL_MEMORY.move_to_end(cache_key)
            while len(_ALBUM_THUMBNAIL_MEMORY) > ALBUM_THUMBNAIL_MEMORY_CAP:
                _ALBUM_THUMBNAIL_MEMORY.popitem(last=False)
        return data

    def _format_album_entry(self, item) -> dict:
        mime_type = self._resolve_album_mime_type(
            item.content, getattr(item.descriptor, "mime_type", ""))
        filename = album_filename(
            item.image_id, item.created_at, item.action, item.content, mime_type=mime_type)
        data = self._album_thumbnail_bytes(item)
        entry = {
            "image_id": item.image_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(item.content),
            "created_at": item.created_at,
            "action": item.action,
            "width": item.descriptor.width,
            "height": item.descriptor.height,
            "thumbnail": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
        }
        local_date = getattr(item, "local_date", "")
        if isinstance(local_date, str) and local_date:
            entry["local_date"] = local_date
        weather = getattr(item, "weather", None)
        if isinstance(weather, dict):
            entry["weather"] = weather
        activities = getattr(item, "activities", None)
        if isinstance(activities, list) and activities:
            entry["activities"] = list(activities)
        return entry

    def _fetch_generated_album_visible(self, context, at, *, start_cursor, limit, hidden):
        current_cursor = start_cursor
        seen_cursors = set()
        visible = []
        next_cursor = None
        for _ in range(8):
            page = self.application.messages.generated_images.album_page(
                context, at, limit=limit, cursor=current_cursor)
            next_cursor = page.get("next_cursor")
            visible = [item for item in page["items"] if item.image_id not in hidden]
            if visible or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            current_cursor = next_cursor
        return visible, next_cursor

    def album(self, operator, context, *, at, cursor=None, limit=ALBUM_LIMIT):
        owner = self._admit(operator, context, at)
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= ALBUM_MAX_LIMIT:
            raise TaskError("generated_album_limit_invalid", "the album page size is outside its bounds")
        hidden = self._hidden_album_ids(context)
        outfit_records = list_daily_outfit_records(self.runtime, owner, hidden_ids=hidden)
        gen_cursor, gen_done, outfit_offset, gen_skip, saved_gen_limit, is_merged = _decode_merged_album_cursor(cursor)

        if not outfit_records and not is_merged:
            visible, next_cursor = self._fetch_generated_album_visible(
                context, at, start_cursor=cursor, limit=limit, hidden=hidden)
            items = [self._format_album_entry(item) for item in visible]
            counter = getattr(self.application.messages.generated_images, "album_count", None)
            if callable(counter):
                counted = counter(context, at, hidden_ids=hidden)
                total = max(counted, len(items)) if (type(counted) is int and counted >= 0) else None
            else:
                total = len(items) if (cursor is None and next_cursor is None) else None
            return {"items": items, "next_cursor": next_cursor, "total": total}

        fetch_limit = saved_gen_limit if (gen_skip > 0 and saved_gen_limit is not None) else limit
        gen_raw = []
        gen_next = None
        seen_refill = set()
        if not gen_done:
            gen_raw, gen_next = self._fetch_generated_album_visible(
                context, at, start_cursor=gen_cursor, limit=fetch_limit, hidden=hidden)
            if gen_skip >= len(gen_raw):
                if gen_next and gen_next not in seen_refill:
                    seen_refill.add(gen_next)
                    gen_cursor = gen_next
                    gen_skip = 0
                    fetch_limit = limit
                    gen_raw, gen_next = self._fetch_generated_album_visible(
                        context, at, start_cursor=gen_cursor, limit=fetch_limit, hidden=hidden)
                else:
                    gen_done = True
                    gen_raw = []
                    gen_next = None
                    gen_skip = 0

        gi = gen_skip
        oi = outfit_offset
        items = []
        while len(items) < limit:
            if not gen_done and gi >= len(gen_raw):
                if gen_next and gen_next not in seen_refill:
                    seen_refill.add(gen_next)
                    gen_cursor = gen_next
                    gen_skip = 0
                    fetch_limit = limit
                    gen_raw, gen_next = self._fetch_generated_album_visible(
                        context, at, start_cursor=gen_cursor, limit=fetch_limit, hidden=hidden)
                    gi = 0
                else:
                    gen_done = True
                    gen_raw = []
                    gen_next = None
                    gen_skip = 0
                    gi = 0
            has_g = (not gen_done) and gi < len(gen_raw)
            has_o = oi < len(outfit_records)
            if not has_g and not has_o:
                break
            if has_g and not has_o:
                items.append(self._format_album_entry(gen_raw[gi]))
                gi += 1
                continue
            if not has_g and has_o:
                loaded = load_daily_outfit_record(self.runtime, owner, outfit_records[oi]["image_id"])
                oi += 1
                if loaded is not None:
                    items.append(self._format_album_entry(_build_daily_outfit_album_item(loaded)))
                continue
            g_item = gen_raw[gi]
            o_rec = outfit_records[oi]
            g_key = (str(g_item.created_at or ""), str(g_item.image_id or ""))
            o_key = (str(o_rec["created_at"] or ""), str(o_rec["image_id"] or ""))
            if o_key >= g_key:
                loaded = load_daily_outfit_record(self.runtime, owner, o_rec["image_id"])
                oi += 1
                if loaded is not None:
                    items.append(self._format_album_entry(_build_daily_outfit_album_item(loaded)))
            else:
                items.append(self._format_album_entry(g_item))
                gi += 1

        if gen_done:
            next_gc, next_gd, next_gs, next_gl = None, True, 0, limit
        elif gi < len(gen_raw):
            encoded_tail = None
            if gi > 0:
                encoder = getattr(self.application.messages.generated_images, "_encode_album_cursor", None)
                if callable(encoder):
                    try:
                        candidate = encoder(gen_raw[gi - 1])
                        if type(candidate) is str and 8 <= len(candidate) <= 1024:
                            encoded_tail = candidate
                    except (AttributeError, IndexError, TypeError, ValueError):
                        encoded_tail = None
            if encoded_tail is not None:
                next_gc, next_gd, next_gs, next_gl = encoded_tail, False, 0, limit
            else:
                next_gc, next_gd, next_gs, next_gl = gen_cursor, False, gi, fetch_limit
        elif gen_next:
            next_gc, next_gd, next_gs, next_gl = gen_next, False, 0, limit
        else:
            next_gc, next_gd, next_gs, next_gl = None, True, 0, limit

        has_more = (not next_gd) or (oi < len(outfit_records))
        next_cursor = (
            _encode_merged_album_cursor(next_gc, next_gd, oi, next_gs, next_gl)
            if has_more else None
        )
        counter = getattr(self.application.messages.generated_images, "album_count", None)
        if callable(counter):
            counted = counter(context, at, hidden_ids=hidden)
            if type(counted) is int and counted >= 0:
                total = max(counted + len(outfit_records), len(items))
            else:
                total = None
        else:
            total = len(items) if (cursor is None and next_cursor is None) else None
        return {"items": items, "next_cursor": next_cursor, "total": total}

    def album_image(self, operator, context, *, at, image_id):
        owner = self._admit(operator, context, at)
        if type(image_id) is not str or not 1 <= len(image_id) <= 180:
            raise TaskError("generated_image_missing", "select one image from this role's album")
        if image_id in self._hidden_album_ids(context):
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        item = self._resolve_album_item(owner, context, at, image_id)
        payload = item.content
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_ASSET_BYTES:
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        mime_type = self._resolve_album_mime_type(
            payload, getattr(item.descriptor, "mime_type", ""))
        filename = album_filename(
            item.image_id, item.created_at, item.action, payload, mime_type=mime_type)
        response = {
            "image_id": item.image_id,
            "filename": filename,
            "mime_type": mime_type,
            "width": item.descriptor.width,
            "height": item.descriptor.height,
            "size_bytes": len(payload),
            "created_at": item.created_at,
            "action": item.action,
            "data_url": f"data:{mime_type};base64," + base64.b64encode(payload).decode("ascii"),
        }
        local_date = getattr(item, "local_date", "")
        if isinstance(local_date, str) and local_date:
            response["local_date"] = local_date
        weather = getattr(item, "weather", None)
        if isinstance(weather, dict):
            response["weather"] = weather
        activities = getattr(item, "activities", None)
        if isinstance(activities, list) and activities:
            response["activities"] = list(activities)
        return response

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
        original = self._resolve_album_item(owner, context, at, image_id)
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

    def daily_outfit_photo(self, operator, context, *, at):
        self._admit(operator, context, at)
        return self.application.daily_outfit.status(context, at=at,
            preview=lambda content: self._thumbnail(content, byte_limit=THUMBNAIL_BYTES, size=512))

    def generate_daily_outfit_photo(self, operator, context, *, at, operation_key):
        self._admit(operator, context, at)
        return self.application.daily_outfit.request(context, operation_key=self._operation_key(operation_key))
