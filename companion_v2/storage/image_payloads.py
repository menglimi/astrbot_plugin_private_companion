"""File-backed image bodies with a small SQLite marker.

Generated and inbound images used to be stored as large BLOBs in
``conversation_image_payloads``.  The table keeps the immutable manifest and
authority columns, while the bytes live under the installation data root.
Rows written before the migration still carry their BLOB until the explicit
migration tool rewrites them; reads accept both shapes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

MARKER_PREFIX = b"\x00PCIMG1/"


def image_extension(content: bytes, mime_type: str = "") -> str:
    value = (mime_type or "").lower()
    if value in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return {"image/png": ".png", "image/jpeg": ".jpg",
                "image/webp": ".webp", "image/gif": ".gif"}[value]
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content[:6] in {b"GIF87a", b"GIF89a"}:
        return ".gif"
    return ".bin"


_extension = image_extension


def image_root(data_root: Path) -> Path:
    return Path(data_root) / "media" / "conversation-images"


def stored_image_path(data_root: Path, content: bytes, *, mime_type: str = "") -> Path | None:
    """Return the existing immutable hash-addressed file path if present on disk."""
    if not isinstance(content, bytes) or not content:
        return None
    root = image_root(data_root)
    if not root.is_dir():
        return None
    root_resolved = root.resolve()
    digest = hashlib.sha256(content).hexdigest()
    preferred = image_extension(content, mime_type)
    candidates = (preferred, *(ext for ext in (".png", ".jpg", ".webp", ".gif", ".bin") if ext != preferred))
    for ext in candidates:
        candidate = root / (digest + ext)
        if candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve()
            if resolved.parent != root_resolved or not candidate.is_file():
                continue
            if candidate.stat().st_size == len(content):
                return resolved
        except OSError:
            continue
    return None


def is_marker(value) -> bool:
    return isinstance(value, (bytes, bytearray)) and bytes(value).startswith(MARKER_PREFIX)


def store_image(data_root: Path, content: bytes, *, mime_type: str = "") -> bytes:
    """Write one body atomically and return the marker persisted in SQLite."""
    if not isinstance(content, bytes) or not content:
        raise ValueError("image content must be non-empty bytes")
    if is_marker(content):
        return bytes(content)
    root = image_root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    name = digest + _extension(content, mime_type)
    target = root / name
    if not target.is_file() or target.stat().st_size != len(content):
        handle, temporary = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return MARKER_PREFIX + name.encode("ascii")


def load_image(data_root: Path, value) -> bytes:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes):
        raise ValueError("image payload is not bytes")
    if not is_marker(value):
        return value
    name = value[len(MARKER_PREFIX):].decode("ascii", errors="strict")
    if not name or re.fullmatch(r"[0-9a-f]{64}\.(?:png|jpg|webp|gif|bin)", name) is None:
        raise ValueError("image payload marker is invalid")
    path = image_root(data_root) / name
    return path.read_bytes()


def migration_rows(connection):
    return connection.execute(
        "SELECT parent_id, parent_lineage, parent_revision, position, content "
        "FROM conversation_image_payloads ORDER BY parent_id, parent_revision, position"
    ).fetchall()


def migrate_image_payloads(data_root: Path, connection, *, batch_size: int = 128,
                           commit=None) -> dict:
    """Move legacy BLOBs to files; returns counts for the maintenance report.

    ``commit`` is called after every batch when supplied.  The in-process
    startup migration uses it so a large 700 MB conversion never holds the
    single SQLite writer lock long enough to fail a live reply transaction.
    """
    changed = 0
    skipped = 0
    pending = []
    for row in migration_rows(connection):
        content = row[4]
        if is_marker(content):
            skipped += 1
            continue
        marker = store_image(data_root, bytes(content))
        pending.append((marker, row[0], row[1], row[2], row[3]))
        changed += 1
        if len(pending) >= batch_size:
            connection.executemany(
                "UPDATE conversation_image_payloads SET content = ? "
                "WHERE parent_id = ? AND parent_lineage = ? AND parent_revision = ? AND position = ?",
                pending,
            )
            if callable(commit):
                commit()
            pending.clear()
    if pending:
        connection.executemany(
            "UPDATE conversation_image_payloads SET content = ? "
            "WHERE parent_id = ? AND parent_lineage = ? AND parent_revision = ? AND position = ?",
            pending,
        )
        if callable(commit):
            commit()
    return {"migrated": changed, "already_file_backed": skipped}
