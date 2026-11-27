"""Album storage and projection for images produced by external LLM tools.

External third-party LLM tools (e.g. standalone image-generation plugin tools
or MCP image tools) deliver images outside Companion's native governed tool
receipts.  Rather than fabricating ``astrbot.core-tool.*`` receipts, this
module stores verified static images in the shared content-addressed file
store (``media/conversation-images/``) and persists lightweight metadata under
``runtime_metadata`` with strict ``origin_key`` isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
import re
import sqlite3
import warnings

from PIL import Image as PILImage, UnidentifiedImageError

from companion_v2.conversation.native_tool_results import PREFIX as NATIVE_TOOL_PREFIX, PREFIX_END as NATIVE_TOOL_PREFIX_END
from companion_v2.execution.models import TaskError, encode
from companion_v2.storage.image_payloads import MARKER_PREFIX, image_root, is_marker, load_image, store_image

from .models import ImageDescriptor, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS


EXTERNAL_ALBUM_PREFIX = "external-generated-album.v1:"
EXTERNAL_ALBUM_PREFIX_END = "external-generated-album.v1;"
EXTERNAL_IMAGE_PREFIX = "external-image:"
EXTERNAL_IMAGE_ACTION = "image.external"
EXTERNAL_MAX_BYTES = 20 * 1024 * 1024
EXTERNAL_MIN_EDGE = 256
EXTERNAL_MAX_EDGE = 8192
EXTERNAL_MAX_PIXELS = 64_000_000
EXTERNAL_ALLOWED_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_MARKER_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|webp|gif|bin)$")
_EXTERNAL_ID_RE = re.compile(r"^external-image:([0-9a-f]{8}):([0-9a-f]{16})$")
_MAX_META_TEXT = 2000
_MAX_SMALL_TEXT = 160
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalImageDescriptor:
    """Static single-frame descriptor when a valid external image exceeds native 5 MiB cap."""

    position: int
    mime_type: str
    width: int
    height: int
    byte_count: int
    content_hash: str


def _origin_tag(origin_key: str) -> str:
    lowered = origin_key.lower()
    if len(lowered) >= 8 and re.fullmatch(r"[0-9a-f]+", lowered):
        return lowered[:8]
    return hashlib.sha256(origin_key.encode("utf-8")).hexdigest()[:8]


def external_image_id(origin_key: str, content_hash: str) -> str:
    """Build deterministic ``external-image:<origin8>:<sha256[:16]>`` album ID."""
    return f"{EXTERNAL_IMAGE_PREFIX}{_origin_tag(origin_key)}:{content_hash[:16].lower()}"


def is_external_image_id(image_id: str) -> bool:
    return type(image_id) is str and image_id.startswith(EXTERNAL_IMAGE_PREFIX)


def parse_external_image_id(image_id: str) -> tuple[str, str] | None:
    """Parse ``external-image:<origin8>:<hash16>`` or return ``None``."""
    if type(image_id) is not str:
        return None
    match = _EXTERNAL_ID_RE.fullmatch(image_id)
    if match is None:
        return None
    return match.group(1), match.group(2)


def external_cursor_tiebreaker(image_id: str) -> str:
    """Return a unique per-image tie-breaker string for album cursors."""
    parts = image_id.split(":", 2)
    if len(parts) == 3 and parts[0] == "external-image":
        return f"{parts[1]}:{parts[2]}"
    return parts[1] if len(parts) > 1 else image_id


def normalize_created_at(value: datetime | str | None) -> str:
    """Normalize timestamps to canonical UTC ISO-8601 strings for range scans."""
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        moment = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        moment = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    else:
        raise ValueError("created_at must be a datetime, ISO string, or None")
    return moment.isoformat()


def _clean_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    return cleaned[:limit]


def _build_descriptor(
    *,
    mime_type: str,
    width: int,
    height: int,
    byte_count: int,
    content_hash: str,
):
    if (
        mime_type in EXTERNAL_ALLOWED_MIMES
        and 1 <= width <= 8192
        and 1 <= height <= 8192
        and width * height <= MAX_IMAGE_PIXELS
        and 0 < byte_count <= MAX_IMAGE_BYTES
    ):
        try:
            return ImageDescriptor(
                position=0,
                mime_type=mime_type,
                width=width,
                height=height,
                byte_count=byte_count,
                content_hash=content_hash,
            )
        except TaskError:
            pass
    return ExternalImageDescriptor(
        position=0,
        mime_type=mime_type,
        width=width,
        height=height,
        byte_count=byte_count,
        content_hash=content_hash,
    )


def validate_external_image_bytes(content: bytes) -> tuple[str, int, int, str, str]:
    """Validate static single-frame PNG/JPEG/WEBP bytes within album bounds.

    Returns ``(mime_type, width, height, sha256_hex, md5_hex)`` or raises
    ``ValueError`` when the payload does not meet quality/safety requirements.
    """
    if not isinstance(content, bytes) or not content or len(content) > EXTERNAL_MAX_BYTES:
        raise ValueError("external image bytes must be non-empty and <= 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(BytesIO(content)) as opened:
                mime_type = PILImage.MIME.get(opened.format, "")
                if mime_type not in EXTERNAL_ALLOWED_MIMES:
                    raise ValueError(f"unsupported external image format: {opened.format!r}")
                is_animated = bool(getattr(opened, "is_animated", False))
                n_frames = int(getattr(opened, "n_frames", 1) or 1)
                if is_animated or n_frames != 1:
                    raise ValueError("animated or multi-frame images are excluded from external album")
                width, height = opened.size
                if (
                    type(width) is not int
                    or type(height) is not int
                    or min(width, height) < EXTERNAL_MIN_EDGE
                    or max(width, height) > EXTERNAL_MAX_EDGE
                    or width * height > EXTERNAL_MAX_PIXELS
                ):
                    raise ValueError(f"external image dimensions out of bounds: {width}x{height}")
                opened.verify()
            with PILImage.open(BytesIO(content)) as reloaded:
                reloaded.load()
    except ValueError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        PILImage.DecompressionBombError,
        PILImage.DecompressionBombWarning,
    ) as error:
        raise ValueError("external image payload is corrupt or unreadable") from error
    sha256_hex = hashlib.sha256(content).hexdigest()
    md5_hex = hashlib.md5(content).hexdigest()
    return mime_type, width, height, sha256_hex, md5_hex


def _marker_file_available(data_root, marker_str: str) -> bool:
    if data_root is None or type(marker_str) is not str or not marker_str:
        return False
    try:
        raw = marker_str.encode("ascii")
    except UnicodeEncodeError:
        return False
    if not is_marker(raw):
        return False
    name = raw[len(MARKER_PREFIX):].decode("ascii", errors="ignore")
    if _MARKER_NAME_RE.fullmatch(name) is None:
        return False
    try:
        return (image_root(data_root) / name).is_file()
    except OSError:
        return False


def _audience_signature(audience_doc) -> tuple[str, ...]:
    if not isinstance(audience_doc, dict):
        return ()
    members = audience_doc.get("recipients")
    if not isinstance(members, list):
        members = audience_doc.get("subjects")
    if not isinstance(members, list):
        return ()
    out = []
    for item in members:
        if isinstance(item, dict):
            sid = item.get("subject_id")
            kind = item.get("kind")
            if isinstance(sid, str) and sid:
                out.append(f"{kind}:{sid}")
    return tuple(sorted(out))


def _context_audience_signature(context) -> tuple[str, ...]:
    audience = getattr(context, "audience", None)
    members = getattr(audience, "recipients", None)
    if members is None:
        members = getattr(audience, "subjects", ()) or ()
    out = []
    for subject in members:
        sid = getattr(subject, "subject_id", "")
        kind = getattr(getattr(subject, "kind", None), "value", getattr(subject, "kind", ""))
        if isinstance(sid, str) and sid:
            out.append(f"{kind}:{sid}")
    return tuple(sorted(out))


def _group_audience_key(signature: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in signature if item.startswith("group:"))


def allowed_origin_keys(runtime, context) -> tuple[str, ...]:
    """Return ``origin_key`` values strictly scoped to this conversation & audience.

    Follows historical context_key revisions of the exact same
    ``(role, installation, platform, kind, account, bot, native_scope_id, audience)``
    while strictly isolating distinct peers, groups, and roles.
    For group scopes, matches the shared ``group:`` collective subject so all
    members of the same group see group-generated external images while keeping
    distinct groups and private chats strictly isolated.
    """
    from companion_v2.foundation.refs import ScopeKind

    scope = context.role_binding.scope
    role = context.role_binding.role
    expected_audience = _context_audience_signature(context)
    expected_group_audience = _group_audience_key(expected_audience)
    keys = [context.key]
    read_all = getattr(runtime, "read_all", None)
    if not callable(read_all):
        return tuple(keys)
    try:
        rows = read_all(
            """WITH RECURSIVE keys(k) AS (
                   SELECT MIN(context_key) FROM conversation_native_turns
                   UNION ALL
                   SELECT (SELECT MIN(t.context_key) FROM conversation_native_turns t WHERE t.context_key > keys.k)
                   FROM keys WHERE keys.k IS NOT NULL
               )
               SELECT n.context_key, n.context_json
               FROM conversation_native_turns n
               WHERE n.rowid IN (
                   SELECT (SELECT t.rowid FROM conversation_native_turns t
                           WHERE t.context_key = keys.k
                           ORDER BY t.observed_at DESC, t.rowid DESC LIMIT 1)
                   FROM keys WHERE keys.k IS NOT NULL
               )""",
            (),
        )
    except sqlite3.OperationalError:
        return tuple(keys)
    from companion_v2.storage.native_dedup import resolve_context_json

    for row in rows:
        candidate_key = row["context_key"]
        if not candidate_key or candidate_key in keys:
            continue
        try:
            data = json.loads(resolve_context_json(runtime, row["context_json"]) or "{}")
        except (TypeError, ValueError):
            continue
        if type(data) is not dict:
            continue
        binding = data.get("role_binding")
        if type(binding) is not dict:
            continue
        candidate_role = binding.get("role")
        candidate_scope = binding.get("scope")
        if type(candidate_role) is not dict or type(candidate_scope) is not dict:
            continue
        if candidate_role.get("subject_id") != role.subject_id:
            continue
        if (
            candidate_scope.get("installation_id"),
            candidate_scope.get("platform"),
            candidate_scope.get("kind"),
            candidate_scope.get("account_id"),
            candidate_scope.get("bot_id"),
            candidate_scope.get("native_scope_id"),
        ) != (
            scope.installation_id,
            scope.platform,
            scope.kind.value,
            scope.account_id,
            scope.bot_id,
            scope.native_scope_id,
        ):
            continue
        candidate_sig = _audience_signature(data.get("audience"))
        if scope.kind is ScopeKind.GROUP:
            if expected_group_audience and _group_audience_key(candidate_sig) != expected_group_audience:
                continue
        elif expected_audience and candidate_sig != expected_audience:
            continue
        keys.append(candidate_key)
    return tuple(keys)


def _iter_metadata_rows(runtime, origin_keys: tuple[str, ...]):
    read_all = getattr(runtime, "read_all", None)
    if not callable(read_all):
        return []
    records = []
    for origin_key in origin_keys:
        if not isinstance(origin_key, str) or not origin_key or ":" in origin_key:
            continue
        prefix = f"{EXTERNAL_ALBUM_PREFIX}{origin_key}:"
        prefix_end = f"{EXTERNAL_ALBUM_PREFIX}{origin_key};"
        rows = read_all(
            "SELECT key, value FROM runtime_metadata WHERE key >= ? AND key < ? ORDER BY key DESC",
            (prefix, prefix_end),
        )
        for row in rows:
            try:
                doc = json.loads(row["value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            if doc.get("origin_key") != origin_key:
                continue
            content_hash = doc.get("content_hash")
            marker = doc.get("marker")
            created_at = doc.get("created_at")
            if (
                type(content_hash) is not str
                or _HEX64_RE.fullmatch(content_hash.lower()) is None
                or type(marker) is not str
                or not marker
                or type(created_at) is not str
                or not created_at.strip()
            ):
                continue
            normalized_hash = content_hash.lower()
            image_id = external_image_id(origin_key, normalized_hash)
            records.append((row["key"], image_id, normalized_hash, doc))
    records.sort(
        key=lambda entry: (entry[3]["created_at"], external_cursor_tiebreaker(entry[1]), 0),
        reverse=True,
    )
    return records


def has_duplicate_sha256(runtime, origin_keys: tuple[str, ...], sha256_hex: str) -> str | None:
    """Return an existing album image_id if ``sha256_hex`` already exists in this scope.

    Checks external album entries, native ``astrbot.core-tool.*`` ledger rows,
    and batch ``extension_tool_invocations`` payloads for the same ``origin_keys``.
    """
    target = (sha256_hex or "").lower()
    if _HEX64_RE.fullmatch(target) is None or not origin_keys:
        return None
    # 1) External album ledger
    for _key, image_id, content_hash, _doc in _iter_metadata_rows(runtime, origin_keys):
        if content_hash == target:
            return image_id
    read_all = getattr(runtime, "read_all", None)
    if not callable(read_all):
        return None
    # 2) Native core-tool ledger
    try:
        native_rows = read_all(
            """SELECT json_extract(m.value, '$.invocation_id') AS invocation_id,
                      json_extract(m.value, '$.images') AS images_json
               FROM runtime_metadata m
               WHERE m.key >= ? AND m.key < ?
                 AND json_extract(m.value, '$.state') = 'succeeded'
                 AND json_extract(m.value, '$.origin_key') IN (SELECT value FROM json_each(?))""",
            (NATIVE_TOOL_PREFIX, NATIVE_TOOL_PREFIX_END, encode(tuple(origin_keys))),
        )
        for row in native_rows:
            raw_images = row["images_json"]
            if not raw_images:
                continue
            try:
                images = json.loads(raw_images)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(images, list):
                continue
            for item in images:
                if isinstance(item, dict) and str(item.get("content_hash") or "").lower() == target:
                    pos = item.get("position", 0)
                    inv = row["invocation_id"] or ""
                    return f"generated-image:{inv}:{pos}"
    except sqlite3.OperationalError:
        pass
    # 3) Batch conversation.generated_images ledger (handles double-encoded JSON payload_json)
    origin_set = frozenset(origin_keys)
    try:
        candidate_rows = read_all(
            """SELECT p.position, ps.payload_json
               FROM conversation_image_payloads p
               JOIN sources s
                 ON s.source_id = p.source_id
                AND s.lineage_id = p.source_lineage
                AND s.source_revision = p.source_revision
                AND s.state = 'active'
               JOIN assets a
                 ON a.asset_id = p.asset_id
                AND a.lineage_id = p.asset_lineage
                AND a.asset_revision = p.asset_revision
                AND a.state = 'active'
               JOIN sources ps
                 ON ps.source_id = p.parent_id
                AND ps.lineage_id = p.parent_lineage
                AND ps.source_revision = p.parent_revision
                AND ps.state = 'active'
               WHERE ps.owner = 'conversation.generated_images'
                 AND json_extract(p.descriptor_json, '$.content_hash') = ?""",
            (target,),
        )
        for row in candidate_rows:
            raw_payload = row["payload_json"]
            if not raw_payload:
                continue
            try:
                parsed = json.loads(raw_payload)
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            if parsed.get("origin_key") not in origin_set:
                continue
            invocation_id = parsed.get("invocation_id")
            if isinstance(invocation_id, str) and invocation_id:
                return f"generated-image:{invocation_id}:{row['position']}"
    except sqlite3.OperationalError:
        pass
    return None


def record_external_generated_image(
    runtime,
    *,
    origin_key: str,
    content: bytes,
    created_at: datetime | str | None = None,
    tool_name: str = "",
    prompt: str = "",
    model: str = "",
    size: str = "",
    source_plugin: str = "",
    extra_origin_keys: tuple[str, ...] = (),
) -> dict:
    """Validate, deduplicate, and persist one external tool generated image.

    Returns ``{"status": "recorded" | "duplicate", "image_id": ..., "key": ...,
    "content_hash": ..., "md5": ...}``.
    """
    if type(origin_key) is not str or not origin_key.strip() or ":" in origin_key:
        raise ValueError("origin_key must be a non-empty string without ':'")
    origin_key = origin_key.strip()
    mime_type, width, height, sha256_hex, md5_hex = validate_external_image_bytes(content)
    created_iso = normalize_created_at(created_at)

    dedup_keys = tuple(dict.fromkeys((origin_key, *(k for k in extra_origin_keys if isinstance(k, str) and k))))
    existing_id = has_duplicate_sha256(runtime, dedup_keys, sha256_hex)
    if existing_id is not None:
        return {
            "status": "duplicate",
            "image_id": existing_id,
            "key": None,
            "content_hash": sha256_hex,
            "md5": md5_hex,
        }

    data_root = getattr(runtime, "data_root", None)
    if data_root is None:
        raise ValueError("runtime.data_root is required to store external generated images")
    marker_bytes = store_image(data_root, content, mime_type=mime_type)
    marker_str = marker_bytes.decode("ascii")
    image_id = external_image_id(origin_key, sha256_hex)
    meta_key = f"{EXTERNAL_ALBUM_PREFIX}{origin_key}:{created_iso}:{sha256_hex[:12]}"
    document = {
        "version": 1,
        "origin_key": origin_key,
        "image_id": image_id,
        "created_at": created_iso,
        "action": EXTERNAL_IMAGE_ACTION,
        "marker": marker_str,
        "content_hash": sha256_hex,
        "md5": md5_hex,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "byte_count": len(content),
        "tool_name": _clean_text(tool_name, limit=_MAX_SMALL_TEXT),
        "prompt": _clean_text(prompt, limit=_MAX_META_TEXT),
        "model": _clean_text(model, limit=_MAX_SMALL_TEXT),
        "size": _clean_text(size, limit=64) or f"{width}x{height}",
        "source_plugin": _clean_text(source_plugin, limit=_MAX_SMALL_TEXT),
    }
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with runtime.transaction() as connection:
        # Re-check under the write lock to stay race-free under concurrent workers.
        prefix = f"{EXTERNAL_ALBUM_PREFIX}{origin_key}:"
        prefix_end = f"{EXTERNAL_ALBUM_PREFIX}{origin_key};"
        for row in connection.execute(
            "SELECT value FROM runtime_metadata WHERE key >= ? AND key < ?",
            (prefix, prefix_end),
        ).fetchall():
            try:
                existing = json.loads(row["value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(existing, dict) and str(existing.get("content_hash") or "").lower() == sha256_hex:
                return {
                    "status": "duplicate",
                    "image_id": existing.get("image_id") or image_id,
                    "key": None,
                    "content_hash": sha256_hex,
                    "md5": md5_hex,
                }
        connection.execute(
            "INSERT INTO runtime_metadata(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (meta_key, payload),
        )
    return {
        "status": "recorded",
        "image_id": image_id,
        "key": meta_key,
        "content_hash": sha256_hex,
        "md5": md5_hex,
    }


def backfill_external_generated_image(
    runtime,
    *,
    origin_key: str,
    image_path: str | Path,
    created_at: datetime | str,
    tool_name: str = "",
    prompt: str = "",
    model: str = "",
    size: str = "",
    source_plugin: str = "",
    expected_md5: str = "",
    extra_origin_keys: tuple[str, ...] = (),
) -> dict:
    """Explicitly, idempotently backfill ONE verified image file into one origin_key.

    Never scans directories; callers must supply the exact target ``origin_key``,
    exact file path, and original ``created_at`` timestamp.
    """
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"backfill image file not found: {path}")
    raw = path.read_bytes()
    if expected_md5:
        actual_md5 = hashlib.md5(raw).hexdigest().lower()
        if actual_md5 != expected_md5.strip().lower():
            raise ValueError(f"MD5 mismatch for {path}: expected {expected_md5}, got {actual_md5}")
    return record_external_generated_image(
        runtime,
        origin_key=origin_key,
        content=raw,
        created_at=created_at,
        tool_name=tool_name,
        prompt=prompt,
        model=model,
        size=size,
        source_plugin=source_plugin,
        extra_origin_keys=extra_origin_keys,
    )


def _load_album_item(runtime, image_id: str, content_hash: str, doc: dict):
    from .generated import GeneratedAlbumImage

    data_root = getattr(runtime, "data_root", None)
    if data_root is None:
        return None
    marker = doc.get("marker")
    if type(marker) is not str or not marker:
        return None
    try:
        content = load_image(data_root, marker.encode("ascii"))
    except (TaskError, OSError, ValueError, UnicodeEncodeError):
        return None
    if not isinstance(content, bytes) or not content or len(content) > EXTERNAL_MAX_BYTES:
        return None
    if hashlib.sha256(content).hexdigest() != content_hash:
        return None
    mime_type = doc.get("mime_type") if type(doc.get("mime_type")) is str else ""
    width = doc.get("width") if type(doc.get("width")) is int else 0
    height = doc.get("height") if type(doc.get("height")) is int else 0
    if mime_type not in EXTERNAL_ALLOWED_MIMES or width <= 0 or height <= 0:
        try:
            mime_type, width, height, _, _ = validate_external_image_bytes(content)
        except ValueError:
            return None
    descriptor = _build_descriptor(
        mime_type=mime_type,
        width=width,
        height=height,
        byte_count=len(content),
        content_hash=content_hash,
    )
    return GeneratedAlbumImage(
        image_id=image_id,
        created_at=doc["created_at"],
        action=doc.get("action") if type(doc.get("action")) is str and doc.get("action") else EXTERNAL_IMAGE_ACTION,
        descriptor=descriptor,
        content=content,
    )


def page(owner, context, at, *, before=None, limit=24):
    """Project external-tool generated images into ``GeneratedImages.album_page``."""
    del at
    keys = allowed_origin_keys(owner.runtime, context)
    records = _iter_metadata_rows(owner.runtime, keys)
    items = []
    seen_ids = set()
    for _meta_key, image_id, content_hash, doc in records:
        if image_id in seen_ids:
            continue
        tie = external_cursor_tiebreaker(image_id)
        if before is not None and (doc["created_at"], tie, 0) >= (
            before["created_at"],
            before["invocation_id"],
            before["position"],
        ):
            continue
        item = _load_album_item(owner.runtime, image_id, content_hash, doc)
        if item is None:
            continue
        seen_ids.add(image_id)
        items.append(item)
        if len(items) > limit:
            return items, True
    return items, False


def count(owner, context, *, hidden_ids=()):
    """Count visible external-tool album images without decoding image bytes."""
    keys = allowed_origin_keys(owner.runtime, context)
    hidden = set(hidden_ids or ())
    data_root = getattr(owner.runtime, "data_root", None)
    seen_ids = set()
    for _meta_key, image_id, _content_hash, doc in _iter_metadata_rows(owner.runtime, keys):
        if image_id in hidden or image_id in seen_ids:
            continue
        if not _marker_file_available(data_root, doc.get("marker")):
            continue
        seen_ids.add(image_id)
    return len(seen_ids), seen_ids


def image(owner, context, at, image_id: str):
    """Resolve one exact external-tool album image within the caller's scope."""
    del at
    parsed = parse_external_image_id(image_id)
    if parsed is None:
        raise TaskError("generated_image_missing", "the selected album image is unavailable")
    origin_prefix, hash_prefix = parsed
    keys = allowed_origin_keys(owner.runtime, context)
    matching_keys = tuple(k for k in keys if _origin_tag(k) == origin_prefix)
    if not matching_keys:
        raise TaskError("generated_image_missing", "the selected album image is unavailable")
    for _meta_key, candidate_id, content_hash, doc in _iter_metadata_rows(owner.runtime, matching_keys):
        if candidate_id == image_id and content_hash.startswith(hash_prefix):
            loaded = _load_album_item(owner.runtime, candidate_id, content_hash, doc)
            if loaded is not None:
                return loaded
    raise TaskError("generated_image_missing", "the selected album image is unavailable")


def prompt_excerpt(owner, context, image_id: str, *, max_chars: int = 120) -> str:
    """Return bounded prompt summary for one external-tool album image."""
    parsed = parse_external_image_id(image_id)
    if parsed is None:
        return ""
    origin_prefix, hash_prefix = parsed
    keys = allowed_origin_keys(owner.runtime, context)
    matching_keys = tuple(k for k in keys if _origin_tag(k) == origin_prefix)
    if not matching_keys:
        return ""
    for _meta_key, candidate_id, content_hash, doc in _iter_metadata_rows(owner.runtime, matching_keys):
        if candidate_id == image_id and content_hash.startswith(hash_prefix):
            text = _clean_text(doc.get("prompt"), limit=_MAX_META_TEXT)
            if not text:
                tool_name = _clean_text(doc.get("tool_name"), limit=64)
                text = f"{tool_name} 生成图片" if tool_name else ""
            if len(text) <= max_chars:
                return text
            return text[: max(1, max_chars - 1)].rstrip() + "…"
    return ""


def delete_external_generated_image(runtime, origin_keys: tuple[str, ...], image_id: str) -> bool:
    """Remove an external-tool album metadata record for the exact allowed origin_keys."""
    parsed = parse_external_image_id(image_id)
    if parsed is None or not origin_keys:
        return False
    origin_prefix, hash_prefix = parsed
    matching_keys = tuple(k for k in origin_keys if _origin_tag(k) == origin_prefix)
    removed = False
    with runtime.transaction() as connection:
        for meta_key, candidate_id, content_hash, _doc in _iter_metadata_rows(runtime, matching_keys):
            if candidate_id == image_id and content_hash.startswith(hash_prefix):
                connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (meta_key,))
                removed = True
    return removed


def find_by_md5(runtime, origin_keys: tuple[str, ...], md5_hex: str, *, hidden_ids=()):
    """Find one exact external-tool album image matching a 32-hex MD5 within ``origin_keys``.

    Designed for direct reuse by quoted-image recovery (``core_reply_media.py``).
    Never crosses ``origin_keys`` boundaries and re-verifies SHA-256 and MD5 bytes.
    """
    target = (md5_hex or "").strip().lower()
    if _HEX32_RE.fullmatch(target) is None or not origin_keys:
        return None
    hidden = set(hidden_ids or ())
    for _meta_key, image_id, content_hash, doc in _iter_metadata_rows(runtime, origin_keys):
        if image_id in hidden:
            continue
        stored_md5 = doc.get("md5")
        if type(stored_md5) is str and stored_md5.lower() != target:
            continue
        loaded = _load_album_item(runtime, image_id, content_hash, doc)
        if loaded is None:
            continue
        if hashlib.md5(loaded.content).hexdigest().lower() == target:
            return loaded
    return None
