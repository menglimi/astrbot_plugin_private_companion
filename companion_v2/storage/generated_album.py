"""Role-scoped, human-browsable materialization for generated album images.

The immutable content-addressed hash store under ``media/conversation-images/``
and SQLite payload rows remain the canonical source of truth and are never
mutated or deleted. This module maintains an incremental, browsable folder
under ``media/generated-album/`` with independently editable copies.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .image_payloads import image_extension


GENERATED_ALBUM_DIRNAME = "generated-album"
ALBUM_HIDDEN_KEY_PREFIX = "image.album.hidden.v1:"
_SAFE_ROLE_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_SHORT_ID_SUFFIX_RE = re.compile(r"-([0-9a-f]{12})$")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def hidden_album_ids(runtime, context):
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?",
                           (ALBUM_HIDDEN_KEY_PREFIX + context.key,))
    if row is None:
        return set()
    try:
        values = json.loads(row["value"])
    except (TypeError, ValueError):
        return set()
    return {item for item in values if type(item) is str and item} if isinstance(values, list) else set()


def _is_windows_reserved(name: str) -> bool:
    return name.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES


def safe_role_slug(role_id: str) -> str:
    """Convert any role identifier into a deterministic, path-safe directory name."""
    if not isinstance(role_id, str) or not role_id.strip():
        raise ValueError("role_id must be a non-empty string")
    if (
        _SAFE_ROLE_SLUG_RE.fullmatch(role_id) is not None
        and ".." not in role_id
        and not _is_windows_reserved(role_id)
    ):
        return role_id
    digest = hashlib.sha256(role_id.encode("utf-8")).hexdigest()[:12]
    cleaned = re.sub(r"[^a-z0-9]+", "-", role_id.lower()).strip("-")
    if len(cleaned) > 32:
        cleaned = cleaned[:32].rstrip("-")
    if not cleaned or _is_windows_reserved(cleaned):
        return f"role-{digest}"
    return f"{cleaned}-{digest}"


def generated_album_root(data_root: Path) -> Path:
    """Return ``<data_root>/media/generated-album`` after containment verification."""
    root = Path(data_root).resolve()
    media_dir = root / "media"
    if media_dir.is_symlink():
        raise ValueError("media directory cannot be a symlink")
    album_root = media_dir / GENERATED_ALBUM_DIRNAME
    if album_root.is_symlink():
        raise ValueError("generated album root cannot be a symlink")
    resolved = album_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("generated album root escapes data root")
    return resolved


def role_album_dir(data_root: Path, role_id: str | None = None, *, owner: str | None = None) -> Path:
    """Resolve the role-scoped generated album directory with strict traversal guards."""
    base = generated_album_root(data_root)
    if role_id is None:
        return base
    if not isinstance(role_id, str):
        raise ValueError("role_id must be a string or None")
    if not role_id.strip():
        return base
    slug = safe_role_slug(role_id)
    if owner is not None:
        owner_suffix = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
        slug = f"{slug}-{owner_suffix}"
    candidate = base / slug
    if candidate.is_symlink():
        raise ValueError("generated album role folder cannot be a symlink")
    resolved = candidate.resolve()
    if resolved == base or resolved.parent != base or not resolved.is_relative_to(base):
        raise ValueError("generated album role folder escapes album root")
    return resolved


def album_short_id(image_id: str) -> str:
    """Return a stable 12-hex-char identifier derived from one album image_id."""
    if not isinstance(image_id, str) or not image_id:
        raise ValueError("image_id must be a non-empty string")
    return hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:12]


def _format_timestamp(created_at: str) -> str:
    if isinstance(created_at, str) and created_at.strip():
        try:
            parsed = datetime.fromisoformat(created_at.strip())
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.strftime("%Y%m%d-%H%M%S")
        except ValueError:
            digits = "".join(char for char in created_at if char.isdigit())
            if len(digits) >= 14:
                return f"{digits[:8]}-{digits[8:14]}"
    return "00000000-000000"


def _format_action(action: str) -> str:
    raw = action.strip().lower() if isinstance(action, str) else ""
    if raw.startswith("image."):
        raw = raw[len("image."):]
    cleaned = re.sub(r"[^a-z0-9_]+", "-", raw).strip("-_")
    if len(cleaned) > 24:
        cleaned = cleaned[:24].rstrip("-_")
    return cleaned or "image"


def album_filename(
    image_id: str,
    created_at: str,
    action: str,
    content: bytes,
    *,
    mime_type: str = "",
) -> str:
    """Build ``YYYYMMDD-HHMMSS-<action>-<short_id>.<ext>`` for one album image."""
    stamp = _format_timestamp(created_at)
    action_label = _format_action(action)
    short_id = album_short_id(image_id)
    extension = image_extension(content, mime_type)
    return f"{stamp}-{action_label}-{short_id}{extension}"


def _existing_short_ids(album_dir: Path) -> set[str]:
    existing: set[str] = set()
    if not album_dir.is_dir():
        return existing
    with os.scandir(album_dir) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            stem = Path(entry.name).stem
            match = _SHORT_ID_SUFFIX_RE.search(stem)
            if match is not None:
                existing.add(match.group(1))
    return existing


def materialize_album_image(
    album_dir: Path,
    *,
    filename: str,
    content: bytes,
) -> bool:
    """Atomically write an independent copy if the album file is missing."""
    if not isinstance(content, bytes) or not content:
        raise ValueError("album image content must be non-empty bytes")
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("album image filename must be a single safe filename")
    base = album_dir.resolve()
    target = base / filename
    if target.parent != base:
        raise ValueError("album image target escapes album directory")
    if target.is_symlink():
        raise ValueError("album image target cannot be a symlink")
    if target.exists():
        return False

    handle, temporary = tempfile.mkstemp(prefix=".album-", suffix=".tmp", dir=base)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists() or target.is_symlink():
            return False
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def sync_generated_album_folder(
    data_root: Path,
    entries=(),
    *,
    role_id: str | None = None,
    owner: str | None = None,
) -> dict:
    """Incrementally materialize missing generated album entries for one role."""
    base = generated_album_root(data_root)
    target = role_album_dir(data_root, role_id, owner=owner)
    target.mkdir(parents=True, exist_ok=True)
    # Re-verify containment after directory creation in case of symlink swaps.
    resolved_target = target.resolve()
    if role_id is not None and isinstance(role_id, str) and role_id.strip():
        if resolved_target == base or resolved_target.parent != base or not resolved_target.is_relative_to(base):
            raise ValueError("generated album role folder escapes album root")
    elif not resolved_target.is_relative_to(Path(data_root).resolve()):
        raise ValueError("generated album root escapes data root")

    existing = _existing_short_ids(resolved_target)
    synced_count = 0
    total_count = 0
    for entry in entries:
        image_id = getattr(entry, "image_id", None)
        content = getattr(entry, "content", None)
        if not isinstance(image_id, str) or not image_id or not isinstance(content, bytes) or not content:
            continue
        total_count += 1
        short_id = album_short_id(image_id)
        if short_id in existing:
            continue
        descriptor = getattr(entry, "descriptor", None)
        mime_type = getattr(descriptor, "mime_type", "") if descriptor is not None else ""
        filename = album_filename(
            image_id,
            getattr(entry, "created_at", ""),
            getattr(entry, "action", ""),
            content,
            mime_type=mime_type,
        )
        if materialize_album_image(
            resolved_target,
            filename=filename,
            content=content,
        ):
            synced_count += 1
        existing.add(short_id)

    return {
        "path": str(resolved_target),
        "synced_count": synced_count,
        "total_count": total_count,
    }
