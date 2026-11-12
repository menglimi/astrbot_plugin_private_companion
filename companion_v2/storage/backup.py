"""Whole-installation backup and restore for the V2 data root.

What "the installation" means here
----------------------------------

The V2 runtime root is not one database. It holds the authoritative main database
plus side databases owned by other components (``semantic-catalog.sqlite3``,
``legacy-memory-materials.sqlite3``, ``content_v2/works.sqlite3``, native-extension
databases), content-addressed image originals, novel/works files, the immutable V1
archive copies and the per-extension JSON configuration. A restore that replaced
only the main database left every other store at a newer point in time, so the
installation ended up stitched together from two generations (audit finding D1).

A snapshot is therefore one **bundle**: a sidecar manifest (``companion-v2-<id>.json``)
describing every artifact with its size and digest, plus a ``companion-v2-<id>.bundle``
directory whose ``payload/`` tree mirrors the data root. Snapshot ids, manifests and
bundles are immutable once written; the sidecar is written last and its presence is
what marks a snapshot complete.

Three rules shape the writer:

First, a database is never copied file-by-file. The main database is opened through
``StorageRuntime.read_snapshot()`` in WAL mode, so committed frames can still live in
the ``-wal`` sidecar; auxiliary databases are opened read-only and read through the
SQLite online backup API. Each snapshot is then switched to ``PRAGMA journal_mode =
DELETE`` so the payload file is self-contained and has no sidecar to forget. That
read snapshot borrows the one live connection and therefore holds the runtime's
process-wide lock, so it covers the main database and nothing else: images, novels,
extension configuration and auxiliary databases are copied after the lock is
released (holding it for the whole payload froze every reader — review P1-1).

Second, credentials do not belong in a backup. Known credential stores (device
pairing, MiHome login, trusted browsers, invitations) are excluded, and every JSON
document is rewritten with obvious secret keys removed before it is packaged. An
unreadable JSON document fails the backup instead of being copied blindly. A restore
must not turn that promise into data loss: current credential files are kept, and
the current value of every credential key is merged back into the restored document,
so a restore never silently forgets an API key or a device pairing (review P2-2).

Third, restore is staged, journaled and rolled back. A restore request is recorded
as ``restore-pending.json`` by ``InstallationBackup.restore``; the host applies it at
the next start *before* opening SQLite (the only moment the files may be replaced
without orphaning the shared connection). ``apply_pending`` verifies the whole bundle,
copies the payload into a same-volume journal directory, swaps every artifact with
``os.replace``, prunes post-snapshot state under the owned roots, re-verifies the live
installation, and only then records the commit. Anything that fails before the commit
marker is rolled back from the journal, so the previous installation is byte-for-byte
usable and the pending request survives for a retry.

Version compatibility
---------------------

``companion-v2.installation-backup.v1`` snapshots hold a single main database. They
stay listable and inspectable, and they are restorable **as what they are**: a
main-database-only disaster recovery. That restore replaces ``companion-v2.sqlite3``
and leaves every side database, image, novel and extension configuration at its
current state, so it is a mixed generation by construction: ``inspect`` and
``preview_restore`` report ``restore_scope = main_database_only`` with the warnings
and the list of paths that will not be restored, and ``restore`` refuses to stage one
until the caller confirms that scope explicitly. 7.0.7 wrote its staged requests with
the same format string, so a request left over from that version is refused at
start-up (``backup_pending_legacy``) and has to be re-staged or cancelled from the
backup page — never applied without a visible warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
from uuid import uuid4

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from .management import cleanup_backups


BACKUP_FORMAT = "companion-v2.installation-backup.v2"
LEGACY_BACKUP_FORMAT = "companion-v2.installation-backup.v1"
RESTORE_FORMAT = "companion-v2.restore-request.v1"
JOURNAL_FORMAT = "companion-v2.restore-journal.v1"
BACKUP_DIRECTORY = "backups"
PENDING_FILENAME = "restore-pending.json"
BUNDLE_SUFFIX = ".bundle"
PAYLOAD_DIRECTORY = "payload"
JOURNAL_PREFIX = ".restore-"
DISPLACED_NAME = "companion-v2.sqlite3.replaced"
MAIN_DATABASE = "companion-v2.sqlite3"
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_ARTIFACTS = 200_000
MAX_PATH_LENGTH = 512
MAX_BUNDLE_BYTES = 64 * 1024 * 1024 * 1024
STAGING_MARGIN_BYTES = 16 * 1024 * 1024
STALE_TEMPORARY_SECONDS = 3600.0

# A manifest belongs to one policy revision. Restore only prunes the roots the
# snapshot itself declared, so a snapshot taken before a new top-level store was
# introduced cannot delete that store's newer state by accident.
POLICY_VERSION = 1
# What one snapshot can restore. A whole-installation bundle replaces everything the
# policy owns; a 7.0.7 single-database snapshot can only replace the main database.
SCOPE_COMPLETE = "complete_installation"
SCOPE_MAIN_DATABASE = "main_database_only"
# The warnings a main-database-only restore publishes, in machine-readable form; the
# dashboard page owns the human wording so the API stays language-neutral.
LEGACY_WARNINGS = ("legacy_main_database_only", "side_stores_not_restored", "mixed_generation_risk")
# The paths a main-database-only restore leaves exactly as they are today.
LEGACY_PRESERVES = ("semantic-catalog.sqlite3", "legacy-memory-materials.sqlite3", "configuration/",
                    "content_v2/", "decision/", "extensions/", "legacy-v1-archives/", "screen/", "search/")
# Files at the root of the runtime data directory that are part of the installation.
OWNED_ROOT_FILES = (MAIN_DATABASE, "semantic-catalog.sqlite3", "legacy-memory-materials.sqlite3")
# Root directories the installation fully owns. Adding a new top-level data store
# means adding it here in the same change, otherwise snapshots would omit it.
OWNED_ROOT_DIRECTORIES = ("configuration", "content_v2", "decision", "extensions", "legacy-v1-archives", "screen", "search")
# Derived state that is rebuilt from authoritative rows; never packaged, always
# dropped by a restore so a stale index cannot answer for rows that no longer exist.
DERIVED_ROOT_FILES = ("memory-search.sqlite3",)
# The immutable V1 archive is a byte-for-byte contract (see companion_v2.legacy.archive);
# it is copied verbatim, never re-snapshotted through SQLite.
VERBATIM_PREFIXES = ("legacy-v1-archives/",)
EXCLUDED_DIRECTORY_NAMES = frozenset({"temp", "cache", ".cache", "endpoint_tests", "__pycache__", ".git"})
CREDENTIAL_FILES = frozenset({
    "extensions/game/trusted_browsers.json",
    "extensions/reality/mihome/auth.json",
    "extensions/reality/mihome/state.json",
    "extensions/together/native_v2/invitations.json",
})
CREDENTIAL_SUFFIXES = (".token", ".secret", ".key", ".pem", ".p12", ".pfx", ".jks", ".keystore")
SECRET_KEYS = frozenset({
    "api_key", "apikey", "api_secret", "api_token", "access_key", "access_key_id",
    "access_token", "refresh_token", "id_token", "auth_token", "authorization",
    "bearer", "bearer_token", "token", "bot_token", "password", "passwd", "secret",
    "secret_key", "client_secret", "private_key", "signing_key", "encryption_key",
    "cookie", "cookies", "session_key", "pairing_token", "invitation_code",
    "invite_code", "credential", "credentials", "app_secret",
})
SECRET_KEY_SUFFIXES = ("_token", "_api_key", "_api_secret", "_password", "_secret",
                       "_private_key", "_secret_key", "_credential", "_credentials")

# A snapshot is refused when it claims another installation or another schema
# revision: both mean it cannot faithfully replace this data root.
_REQUIRED_METADATA = ("installation_id", "schema_version")
# A preview reports how much data a restore would replace. These are the tables a
# reader can compare by row count without interpreting any domain meaning.
COUNTED_TABLES = ("memory_atoms", "operations", "configuration_values")


class BackupError(TaskError):
    """A snapshot or restore request cannot be trusted or completed."""


class _NotSQLiteDatabase(Exception):
    """The named file carries a SQLite suffix but is not a SQLite database."""


@dataclass(frozen=True)
class SourceFile:
    """One file the installation owns, as seen just before it is packaged."""

    path: Path
    relative: str
    kind: str
    byte_size: int
    modified_ns: int


@dataclass(frozen=True)
class _Journal:
    """The same-volume working directory of one restore attempt."""

    root: Path

    @property
    def document_path(self) -> Path:
        return self.root / "journal.json"

    @property
    def stage_root(self) -> Path:
        return self.root / "stage"

    @property
    def rollback_root(self) -> Path:
        return self.root / "rollback"

    @property
    def pruned_root(self) -> Path:
        return self.root / "pruned"

    @property
    def superseded_root(self) -> Path:
        return self.root / "superseded"


# -- small shared helpers ----------------------------------------------------


def _is_link(path: Path) -> bool:
    """True for symlinks and Windows reparse points (junctions, mount points)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _sha256(path: Path) -> str:
    accumulator = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            accumulator.update(block)
    return accumulator.hexdigest()


def _stamp(at: datetime) -> str:
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise BackupError("backup_clock_invalid", "a backup needs a timezone-aware clock")
    return at.astimezone(timezone.utc).isoformat()


def _parse_stamp(value: object) -> datetime:
    if type(value) is not str or not value:
        raise BackupError("backup_snapshot_unreadable", "the snapshot timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot timestamp is malformed") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BackupError("backup_snapshot_unreadable", "the snapshot timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _valid_snapshot_id(value: object) -> str:
    if (type(value) is not str or len(value) != 32
            or any(character not in "0123456789abcdef" for character in value)):
        raise BackupError("backup_snapshot_id_invalid", "a snapshot id is a lowercase hex digest")
    return value


def _snapshot_paths(directory: Path, snapshot_id: str) -> tuple[Path, Path, Path]:
    """Return ``(sidecar, bundle, payload)`` for one snapshot id."""
    snapshot_id = _valid_snapshot_id(snapshot_id)
    sidecar = Path(directory) / f"companion-v2-{snapshot_id}.json"
    bundle = Path(directory) / f"companion-v2-{snapshot_id}{BUNDLE_SUFFIX}"
    return sidecar, bundle, bundle / PAYLOAD_DIRECTORY


def _legacy_database_path(directory: Path, snapshot_id: str) -> Path:
    return Path(directory) / f"companion-v2-{_valid_snapshot_id(snapshot_id)}.sqlite3"


def _parts(relative: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(relative).parts)


def _live_path(data_root: Path, relative: str) -> Path:
    return Path(data_root).joinpath(*_parts(relative))


def _remove_tree(path: Path) -> None:
    """Delete a bundle or journal directory without following links.

    ``shutil.rmtree`` follows Windows junctions and would delete the *link target's*
    contents, so deletion is done entry by entry and a reparse point is removed as a
    link. Best effort: a read-only entry is retried once, a failure is not fatal for
    scratch cleanup.
    """
    target = Path(path)
    if _is_link(target) or target.is_file():
        for _attempt in range(2):
            try:
                target.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                try:
                    os.chmod(target, stat.S_IWRITE)
                except OSError:
                    return
        return
    if not target.is_dir():
        return
    try:
        children = list(target.iterdir())
    except OSError:
        return
    for child in children:
        _remove_tree(child)
    try:
        target.rmdir()
    except OSError:
        try:
            os.chmod(target, stat.S_IWRITE)
            target.rmdir()
        except OSError:
            pass


def _write_json_atomic(path: Path, document: dict) -> None:
    """Publish a JSON document with one atomic replace, never half-written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_verified(source: Path, destination: Path, *, expected_bytes: int | None = None,
                   expected_hash: str | None = None) -> int:
    """Copy one file and prove the copy matches before anybody trusts it."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest_value = hashlib.sha256()
    written = 0
    try:
        with source.open("rb") as reader, destination.open("wb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
                digest_value.update(block)
                written += len(block)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    if expected_bytes is not None and written != expected_bytes:
        destination.unlink(missing_ok=True)
        raise OSError(f"copy size mismatch for {source.name}")
    if expected_hash is not None and digest_value.hexdigest() != expected_hash:
        destination.unlink(missing_ok=True)
        raise OSError(f"copy digest mismatch for {source.name}")
    return written


# -- reading snapshots -------------------------------------------------------


def _readonly_connection(path: Path) -> sqlite3.Connection:
    """Open a file that must not change while it is inspected.

    ``immutable=1`` keeps SQLite from creating ``-wal``/``-shm`` next to a snapshot
    (a plain read-only open of a WAL-flagged database does create them), so
    inspection never mutates a bundle, a staged copy or the restored installation.
    """
    from .sqlite_path import readonly_uri
    return sqlite3.connect(readonly_uri(path) + "&immutable=1", uri=True, timeout=10.0)


def _live_connection(path: Path) -> sqlite3.Connection:
    """Open the live data root read-only, honouring a hot WAL written by a previous run.

    ``immutable=1`` would ignore uncheckpointed WAL frames, which is exactly where a
    committed schema migration can sit after a crash, so this path stays a normal
    read-only open and accepts the empty ``-wal``/``-shm`` sidecars SQLite creates.
    """
    from .sqlite_path import readonly_uri
    return sqlite3.connect(readonly_uri(path), uri=True, timeout=10.0)


def _read_metadata(connection: sqlite3.Connection) -> dict:
    """Read the identity rows a snapshot must carry to be restorable here."""
    try:
        rows = connection.execute(
            "SELECT key, value FROM runtime_metadata WHERE key IN (?, ?)", _REQUIRED_METADATA,
        ).fetchall()
    except sqlite3.Error as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot is not a V2 data root") from error
    found = {row["key"]: row["value"] for row in rows}
    if set(found) != set(_REQUIRED_METADATA):
        raise BackupError("backup_snapshot_incomplete", "the snapshot is missing its installation identity")
    return found


def _database_details(path: Path, *, installation_id: str | None = None,
                      schema_version: str | None = None, foreign_keys: bool = False) -> dict:
    """Prove one SQLite file opens, is internally consistent and is ours."""
    try:
        connection = _readonly_connection(path)
    except sqlite3.Error as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot database cannot be opened") from error
    try:
        connection.row_factory = sqlite3.Row
        if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
            raise BackupError("backup_snapshot_corrupt", "a snapshot database failed its integrity check")
        metadata = None
        if installation_id is not None or schema_version is not None:
            metadata = _read_metadata(connection)
            if installation_id is not None and metadata["installation_id"] != installation_id:
                raise BackupError("backup_snapshot_foreign", "the snapshot belongs to another installation")
            if schema_version is not None and metadata["schema_version"] != schema_version:
                raise BackupError("backup_snapshot_schema_mismatch",
                                  "the snapshot schema revision differs from this data root")
        if foreign_keys and connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("backup_snapshot_broken_references", "the snapshot contains broken references")
        table_count = connection.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        ).fetchone()["n"]
    except sqlite3.Error as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot could not be verified") from error
    finally:
        connection.close()
    return {"table_count": table_count,
            "installation_id": metadata["installation_id"] if metadata else None,
            "schema_version": metadata["schema_version"] if metadata else None}


def _policy_document() -> dict:
    return {"version": POLICY_VERSION,
            "roots": [*OWNED_ROOT_FILES, *OWNED_ROOT_DIRECTORIES],
            "derived": list(DERIVED_ROOT_FILES)}


def _schema_version_in(connection: sqlite3.Connection) -> str:
    """Read the schema revision from the connection that is being snapshotted.

    It must come from the same read transaction as the main database copy: a later
    ``read_one`` could see a migrated revision that the snapshot does not contain.
    """
    row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise BackupError("backup_metadata_missing", "this data root has no schema version")
    return row["value"]


def _current_roots() -> tuple[str, ...]:
    return (*OWNED_ROOT_FILES, *OWNED_ROOT_DIRECTORIES)


def _safe_relative_path(value: object, roots: tuple[str, ...] | list[str]) -> str:
    """Accept only a normalized relative POSIX path inside a declared root."""
    if type(value) is not str or not value or len(value) > MAX_PATH_LENGTH:
        raise BackupError("backup_snapshot_incomplete", "an artifact path is missing or too long")
    if "\\" in value or ":" in value or any(ord(character) < 32 for character in value):
        raise BackupError("backup_snapshot_incomplete", "an artifact path is not a relative POSIX path")
    if value.startswith("/") or value.endswith("/"):
        raise BackupError("backup_snapshot_incomplete", "an artifact path is not a relative POSIX path")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).as_posix() != value:
        raise BackupError("backup_snapshot_incomplete", "an artifact path is not a normalized relative path")
    if not any(value == root or value.startswith(root + "/") for root in roots):
        raise BackupError("backup_snapshot_incomplete", "an artifact path is outside the installation roots")
    return value


def _validated_artifacts(document: dict) -> tuple[dict, ...]:
    """Validate the artifact list of a manifest without touching the payload."""
    if document.get("format") != BACKUP_FORMAT:
        raise BackupError("backup_snapshot_legacy", "the snapshot is not a whole-installation bundle")
    policy = document.get("policy")
    if type(policy) is not dict:
        raise BackupError("backup_snapshot_incomplete", "the snapshot has no storage policy")
    if type(policy.get("version")) is not int or policy["version"] < 1:
        raise BackupError("backup_snapshot_incomplete", "the snapshot storage policy is invalid")
    roots = policy.get("roots")
    if type(roots) is not list or not roots or not all(type(item) is str for item in roots):
        raise BackupError("backup_snapshot_incomplete", "the snapshot storage policy is invalid")
    current = _current_roots()
    if not set(roots) <= set(current):
        raise BackupError("backup_snapshot_policy_mismatch",
                          "the snapshot was taken by a different installation layout")
    derived = policy.get("derived", [])
    if type(derived) is not list or not set(derived) <= set(DERIVED_ROOT_FILES):
        raise BackupError("backup_snapshot_policy_mismatch",
                          "the snapshot derived-state policy is not understood by this version")
    artifacts = document.get("artifacts")
    if type(artifacts) is not list or not artifacts or len(artifacts) > MAX_ARTIFACTS:
        raise BackupError("backup_snapshot_incomplete", "the snapshot has no usable artifact list")
    validated = []
    seen = set()
    total = 0
    for item in artifacts:
        if type(item) is not dict:
            raise BackupError("backup_snapshot_incomplete", "an artifact entry is malformed")
        relative = _safe_relative_path(item.get("path"), roots)
        if relative in seen:
            raise BackupError("backup_snapshot_incomplete", "the snapshot lists an artifact twice")
        seen.add(relative)
        kind = item.get("kind")
        if kind not in {"sqlite", "file"}:
            raise BackupError("backup_snapshot_incomplete", "an artifact entry has an unknown kind")
        size = item.get("bytes")
        if type(size) is not int or size < 0 or size > MAX_ARTIFACT_BYTES:
            raise BackupError("backup_snapshot_size_invalid", "an artifact size is outside the accepted range")
        content_hash = item.get("sha256")
        if (type(content_hash) is not str or len(content_hash) != 64
                or any(character not in "0123456789abcdef" for character in content_hash)):
            raise BackupError("backup_snapshot_incomplete", "an artifact digest is malformed")
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise BackupError("backup_snapshot_size_invalid", "the snapshot is larger than the accepted range")
        validated.append({"path": relative, "kind": kind, "bytes": size, "sha256": content_hash})
    if MAIN_DATABASE not in seen:
        raise BackupError("backup_snapshot_incomplete", "the snapshot does not contain the main database")
    return tuple(validated)


def _canonical_hash(document: dict, artifacts: tuple[dict, ...] | list[dict]) -> str:
    material = {
        "format": document.get("format"),
        "snapshot_id": document.get("snapshot_id"),
        "created_at": document.get("created_at"),
        "installation_id": document.get("installation_id"),
        "schema_version": document.get("schema_version"),
        "policy": document.get("policy"),
        "artifacts": [[item["path"], item["kind"], item["bytes"], item["sha256"]] for item in artifacts],
    }
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _verify_identity(document: dict, *, installation_id: str, schema_version: str) -> None:
    if document.get("installation_id") != installation_id:
        raise BackupError("backup_snapshot_foreign", "the snapshot belongs to another installation")
    if document.get("schema_version") != schema_version:
        raise BackupError("backup_snapshot_schema_mismatch",
                          "the snapshot schema revision differs from this data root")


def _verify_payload(payload_root: Path, artifacts: tuple[dict, ...] | list[dict],
                    *, installation_id: str | None = None, schema_version: str | None = None) -> dict:
    """Prove every artifact byte exists, matches and, for databases, opens cleanly."""
    payload_root = Path(payload_root)
    if _is_link(payload_root) or not payload_root.is_dir():
        raise BackupError("backup_snapshot_missing", "the snapshot payload is missing")
    expected = set()
    byte_size = 0
    for item in artifacts:
        relative = item["path"]
        expected.add(relative)
        target = payload_root.joinpath(*_parts(relative))
        if _is_link(target):
            raise BackupError("backup_snapshot_incomplete", "the snapshot payload contains a link")
        if not target.is_file():
            raise BackupError("backup_snapshot_incomplete", "the snapshot payload is missing an artifact")
        try:
            size = target.stat().st_size
        except OSError as error:
            raise BackupError("backup_snapshot_incomplete", "the snapshot payload cannot be read") from error
        if size != item["bytes"]:
            raise BackupError("backup_snapshot_incomplete", "an artifact size does not match its manifest entry")
        if _sha256(target) != item["sha256"]:
            raise BackupError("backup_snapshot_corrupt", "an artifact digest does not match its manifest entry")
        if item["kind"] == "sqlite":
            details = _database_details(
                target,
                installation_id=installation_id if relative == MAIN_DATABASE else None,
                schema_version=schema_version if relative == MAIN_DATABASE else None,
                foreign_keys=relative == MAIN_DATABASE)
            if relative == MAIN_DATABASE:
                if details["installation_id"] != installation_id or details["schema_version"] != schema_version:
                    raise BackupError("backup_snapshot_incomplete",
                                      "the main database identity does not match the manifest")
        byte_size += size
    for current, directories, files in os.walk(payload_root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            if _is_link(current_path / name):
                raise BackupError("backup_snapshot_incomplete", "the snapshot payload contains a link")
        for name in files:
            candidate = current_path / name
            relative = candidate.relative_to(payload_root).as_posix()
            if _is_link(candidate):
                raise BackupError("backup_snapshot_incomplete", "the snapshot payload contains a link")
            if relative not in expected:
                raise BackupError("backup_snapshot_incomplete",
                                  f"the snapshot payload carries an undeclared file: {relative}")
    return {"byte_size": byte_size, "artifact_count": len(expected)}


def _read_snapshot_manifest(directory: Path, snapshot_id: str) -> dict:
    """Read one sidecar and prove it describes the snapshot that was asked for."""
    sidecar, _bundle, _payload = _snapshot_paths(directory, snapshot_id)
    if _is_link(sidecar):
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest is a link")
    if not sidecar.is_file():
        raise BackupError("backup_snapshot_missing", "the named snapshot does not exist")
    try:
        document = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest cannot be read") from error
    if type(document) is not dict or document.get("snapshot_id") != snapshot_id:
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest is malformed")
    if document.get("format") not in {BACKUP_FORMAT, LEGACY_BACKUP_FORMAT}:
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest has an unknown format")
    return document


def _verify_bundle(document: dict, *, directory: Path, installation_id: str,
                   schema_version: str) -> dict:
    """Full verification of one ``installation-backup.v2`` bundle."""
    artifacts = _validated_artifacts(document)
    _verify_identity(document, installation_id=installation_id, schema_version=schema_version)
    _parse_stamp(document.get("created_at"))
    if document.get("content_hash") != _canonical_hash(document, artifacts):
        raise BackupError("backup_snapshot_changed", "the snapshot manifest no longer matches its digest")
    _sidecar, bundle, payload = _snapshot_paths(directory, document["snapshot_id"])
    if _is_link(bundle) or not bundle.is_dir():
        raise BackupError("backup_snapshot_missing", "the snapshot bundle is missing")
    verified = _verify_payload(payload, artifacts, installation_id=installation_id,
                               schema_version=schema_version)
    if document.get("byte_size") != verified["byte_size"]:
        raise BackupError("backup_snapshot_incomplete", "the snapshot byte size does not match its manifest")
    unmanaged = document.get("unmanaged_roots", [])
    if type(unmanaged) is not list or not all(type(item) is str for item in unmanaged):
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest is malformed")
    policy = document["policy"]
    return {**verified, "format": BACKUP_FORMAT, "installation_id": installation_id,
            "schema_version": schema_version, "policy_version": policy["version"],
            "restorable": True, "restore_scope": SCOPE_COMPLETE,
            "warnings": (), "preserves": (),
            "artifacts": artifacts,
            "unmanaged_roots": tuple(item for item in unmanaged if item not in policy["roots"])}


def _verify_legacy(document: dict, *, directory: Path, installation_id: str,
                   schema_version: str) -> dict:
    """Verify a v1 single-database snapshot: restorable, main database only."""
    snapshot_id = _valid_snapshot_id(document.get("snapshot_id"))
    _verify_identity(document, installation_id=installation_id, schema_version=schema_version)
    path = _legacy_database_path(directory, snapshot_id)
    if _is_link(path):
        raise BackupError("backup_snapshot_unreadable", "the snapshot database is a link")
    if not path.is_file():
        raise BackupError("backup_snapshot_missing", "the named snapshot does not exist")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise BackupError("backup_snapshot_unreadable", "the snapshot cannot be read") from error
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise BackupError("backup_snapshot_size_invalid", "the snapshot size is outside the accepted range")
    content_hash = document.get("content_hash")
    if (type(content_hash) is not str or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)):
        raise BackupError("backup_snapshot_unreadable", "the snapshot manifest is malformed")
    if _sha256(path) != content_hash:
        raise BackupError("backup_snapshot_changed", "the snapshot no longer matches its recorded digest")
    details = _database_details(path, installation_id=installation_id, schema_version=schema_version,
                                foreign_keys=True)
    return {"format": LEGACY_BACKUP_FORMAT, "byte_size": size, "table_count": details["table_count"],
            "installation_id": installation_id, "schema_version": schema_version,
            "artifact_count": 1, "restorable": True, "restore_scope": SCOPE_MAIN_DATABASE,
            "warnings": LEGACY_WARNINGS, "preserves": LEGACY_PRESERVES,
            "artifacts": ({"path": MAIN_DATABASE, "kind": "sqlite", "bytes": size,
                           "sha256": content_hash},)}


def _bundle_complete(directory: Path, document: dict) -> bool:
    """Cheap completeness: every declared artifact exists with its declared size."""
    try:
        artifacts = _validated_artifacts(document)
        _sidecar, bundle, payload = _snapshot_paths(directory, document["snapshot_id"])
        if _is_link(bundle) or not bundle.is_dir() or _is_link(payload) or not payload.is_dir():
            return False
        for item in artifacts:
            target = payload.joinpath(*_parts(item["path"]))
            if _is_link(target) or not target.is_file() or target.stat().st_size != item["bytes"]:
                return False
    except (BackupError, OSError, KeyError, TypeError):
        return False
    return True


def _bundle_listable(directory: Path, document: dict) -> bool:
    """A listing check that touches three paths, not twenty thousand.

    It proves the manifest parses, the bundle directory exists and the main
    database payload is there with its declared size. Every other artifact is
    verified by ``inspect``/``restore`` only: a page load must not stat (or hash)
    a whole payload tree (review P2-3).
    """
    try:
        artifacts = _validated_artifacts(document)
        _sidecar, bundle, payload = _snapshot_paths(directory, document["snapshot_id"])
    except (BackupError, KeyError, TypeError):
        return False
    if _is_link(bundle) or not bundle.is_dir() or _is_link(payload) or not payload.is_dir():
        return False
    main_entry = next((item for item in artifacts if item["path"] == MAIN_DATABASE), None)
    if main_entry is None:
        return False
    main = payload.joinpath(*_parts(MAIN_DATABASE))
    try:
        return not _is_link(main) and main.is_file() and main.stat().st_size == main_entry["bytes"]
    except OSError:
        return False


def _legacy_listable(directory: Path, document: dict) -> bool:
    """Cheap presence/size check for a v1 pair; the digest belongs to ``inspect``."""
    try:
        path = _legacy_database_path(directory, document.get("snapshot_id"))
    except BackupError:
        return False
    if _is_link(path) or not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        return False
    declared = document.get("byte_size")
    return type(declared) is not int or declared == size


# The summary a list needs. ``inspect`` still returns the authoritative manifest.
LISTING_FIELDS = ("snapshot_id", "format", "created_at", "note", "byte_size", "artifact_count",
                  "installation_id", "schema_version", "content_hash")


def _listing_document(document: dict, *, restorable: bool, restore_scope: str) -> dict:
    """Project one sidecar down to what a list can display.

    Returning the whole manifest made one page load ship every artifact path and
    digest of every snapshot, and the panel only ever reads the summary fields.
    """
    projected = {field: document.get(field) for field in LISTING_FIELDS}
    artifacts = document.get("artifacts")
    if type(projected["artifact_count"]) is not int:
        projected["artifact_count"] = len(artifacts) if isinstance(artifacts, list) else None
    projected["restorable"] = restorable
    projected["restore_scope"] = restore_scope
    return projected


def _listing_problems(directory: Path) -> tuple[dict, ...]:
    """Sidecars in this directory that a listing has to report instead of showing.

    Only structural damage is detected here (no bundle, no main database payload).
    Deeper damage is found by ``inspect`` or by the copy/retention scan, so the
    listing stays cheap while a broken pair is still visible and repairable.
    """
    directory = Path(directory)
    try:
        sidecars = sorted(directory.glob("companion-v2-*.json"))
    except OSError:
        return ()
    problems: list[dict] = []
    for sidecar in sidecars:
        if _is_link(sidecar):
            continue
        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append({"snapshot_id": "", "reason": "manifest_unreadable",
                             "sidecar": sidecar.name})
            continue
        if type(document) is not dict or type(document.get("snapshot_id")) is not str:
            problems.append({"snapshot_id": "", "reason": "manifest_invalid",
                             "sidecar": sidecar.name})
            continue
        snapshot_id = document["snapshot_id"]
        if document.get("format") == BACKUP_FORMAT:
            if not _bundle_listable(directory, document):
                problems.append({"snapshot_id": snapshot_id, "reason": "bundle_or_main_database_missing",
                                 "sidecar": sidecar.name})
        elif document.get("format") == LEGACY_BACKUP_FORMAT:
            if not _legacy_listable(directory, document):
                problems.append({"snapshot_id": snapshot_id, "reason": "snapshot_database_missing",
                                 "sidecar": sidecar.name})
    return tuple(problems)


def _read_pending_request(path: Path) -> dict:
    """Parse the staged restore request, distinguishing the pre-v2 format."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError("backup_pending_unreadable", "the staged restore request cannot be read") from error
    if type(document) is not dict:
        raise BackupError("backup_pending_invalid", "the staged restore request is malformed")
    if document.get("format") == LEGACY_BACKUP_FORMAT:
        # 7.0.7 staged restores with the same format string it wrote snapshots in.
        # Applying one now would be a main-database-only restore started without the
        # operator seeing that warning, so it is refused; the backup page can cancel
        # it and re-stage the snapshot with the scope spelled out.
        raise BackupError("backup_pending_legacy",
                          "a staged restore written by the previous single-database format cannot be "
                          "applied; cancel it from the backup page and stage the snapshot again")
    if (document.get("format") != RESTORE_FORMAT or type(document.get("snapshot_id")) is not str
            or type(document.get("content_hash")) is not str):
        raise BackupError("backup_pending_invalid", "the staged restore request is malformed")
    _valid_snapshot_id(document["snapshot_id"])
    scope = document.get("restore_scope", SCOPE_COMPLETE)
    if scope not in {SCOPE_COMPLETE, SCOPE_MAIN_DATABASE}:
        raise BackupError("backup_pending_invalid", "the staged restore scope is unknown")
    return {**document, "restore_scope": scope}



# -- snapshot writer ---------------------------------------------------------


def _is_json_document(relative: str) -> bool:
    return relative.casefold().endswith(".json")


def _is_verbatim(relative: str) -> bool:
    return relative.startswith(VERBATIM_PREFIXES)


def _kind_for(relative: str) -> str:
    if _is_verbatim(relative):
        return "file"
    return "sqlite" if relative.casefold().endswith(SQLITE_SUFFIXES) else "file"


def _is_secret_key(key: object) -> bool:
    if type(key) is not str:
        return False
    normalized = key.strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    if normalized in SECRET_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in SECRET_KEY_SUFFIXES)


def _is_credential_path(relative: str, name: str) -> bool:
    """True for a path this module never packages because it holds a credential."""
    return relative in CREDENTIAL_FILES or name.casefold().endswith(CREDENTIAL_SUFFIXES)


def _secret_paths(document: object, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
    """Every credential-named key in one JSON document, with its path."""
    found: list[tuple[tuple[str, ...], object]] = []
    if isinstance(document, dict):
        for key, value in document.items():
            if _is_secret_key(key):
                found.append(((*prefix, str(key)), value))
                continue
            found.extend(_secret_paths(value, (*prefix, str(key))))
    elif isinstance(document, list):
        for index, item in enumerate(document):
            found.extend(_secret_paths(item, (*prefix, str(index))))
    return found


def _apply_secret(document: object, path: tuple[str, ...], value: object) -> bool:
    """Set one dotted path if its container still exists; never invents structure."""
    if not path:
        return False
    head, rest = path[0], path[1:]
    if isinstance(document, dict):
        if not rest:
            document[head] = value
            return True
        child = document.get(head)
        return _apply_secret(child, rest, value) if isinstance(child, (dict, list)) else False
    if isinstance(document, list):
        try:
            index = int(head)
        except ValueError:
            return False
        if not 0 <= index < len(document):
            return False
        if not rest:
            document[index] = value
            return True
        child = document[index]
        return _apply_secret(child, rest, value) if isinstance(child, (dict, list)) else False
    return False


def _merge_current_credentials(restored: Path, current: Path) -> list[str]:
    """Put today's credential values back into a restored JSON document.

    A snapshot deliberately carries no secrets, so placing the redacted copy over
    the live file would forget every key the installation still uses. Each current
    credential value is merged back (the snapshot's non-secret state is untouched)
    and the merged key paths are returned so the restore can report them.
    """
    try:
        current_document = json.loads(current.read_bytes())
        restored_document = json.loads(restored.read_bytes())
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(restored_document, dict):
        return []
    merged: list[str] = []
    for path, value in _secret_paths(current_document):
        if _apply_secret(restored_document, path, value):
            merged.append(".".join(path))
    if not merged:
        return []
    payload = json.dumps(restored_document, ensure_ascii=False, sort_keys=True,
                         indent=2).encode("utf-8") + b"\n"
    with restored.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return sorted(merged)


def _current_credential_files(data_root: Path) -> list[str]:
    """Credential files the installation holds right now, relative to the data root."""
    root = Path(data_root)
    found: list[str] = []
    for entry in OWNED_ROOT_DIRECTORIES:
        base = root / entry
        if not base.is_dir() or _is_link(base):
            continue
        for current, directories, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            directories[:] = [name for name in directories
                              if not _is_link(current_path / name)
                              and name.casefold() not in EXCLUDED_DIRECTORY_NAMES]
            for name in files:
                relative = (current_path / name).relative_to(root).as_posix()
                if _is_credential_path(relative, name):
                    found.append(relative)
    return sorted(found)



def _redact_value(value: object) -> tuple[object, list[str]]:
    """Drop obvious credential keys from one JSON document, at any depth."""
    if isinstance(value, dict):
        clean: dict = {}
        removed: list[str] = []
        for key, item in value.items():
            if _is_secret_key(key):
                removed.append(str(key))
                continue
            cleaned, nested = _redact_value(item)
            clean[key] = cleaned
            removed.extend(f"{key}.{name}" for name in nested)
        return clean, removed
    if isinstance(value, list):
        cleaned_items = []
        removed = []
        for item in value:
            cleaned, nested = _redact_value(item)
            cleaned_items.append(cleaned)
            removed.extend(nested)
        return cleaned_items, removed
    return value, []


def _redact_json(source: Path, target: Path, *, relative: str) -> int:
    """Rewrite one JSON document without its credential keys."""
    try:
        raw = source.read_bytes()
        document = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as error:
        raise BackupError("backup_config_unreadable",
                          f"{relative} is not readable JSON; refusing to package it blindly") from error
    cleaned, removed = _redact_value(document)
    payload = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise BackupError("backup_snapshot_size_invalid", f"{relative} is larger than the accepted range")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return len(removed)


def _open_source_database(source: Path) -> sqlite3.Connection:
    """Read-only where possible; recover a hot WAL only when read-only is refused."""
    try:
        from .sqlite_path import readonly_uri
        connection = sqlite3.connect(readonly_uri(source), uri=True, timeout=10.0)
        connection.execute("PRAGMA schema_version").fetchone()
        return connection
    except sqlite3.Error:
        pass
    try:
        return sqlite3.connect(str(source), timeout=10.0)
    except sqlite3.Error as error:
        raise BackupError("backup_database_unreadable", f"{source.name} cannot be opened") from error


def _snapshot_database(source: Path, target: Path, *,
                       connection: sqlite3.Connection | None = None) -> None:
    """Write one self-contained SQLite snapshot through the online backup API."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    handle = None
    try:
        if connection is not None:
            origin = connection
        else:
            handle = _open_source_database(source)
            origin = handle
        destination = sqlite3.connect(str(target))
        try:
            origin.backup(destination)
            destination.commit()
            # A self-contained payload: no WAL flag, no sidecar to forget.
            mode = destination.execute("PRAGMA journal_mode = DELETE").fetchone()
            if mode is None or str(mode[0]).casefold() != "delete":
                raise BackupError("backup_write_failed", "the snapshot could not be made self-contained")
        finally:
            destination.close()
    except _NotSQLiteDatabase:
        raise
    except sqlite3.Error as error:
        if "not a database" in str(error).casefold():
            raise _NotSQLiteDatabase(str(error)) from error
        raise BackupError("backup_write_failed", f"{source.name} could not be snapshotted") from error
    finally:
        if handle is not None:
            handle.close()


def _drop_sidecars(path: Path) -> None:
    """Remove the journal/WAL siblings SQLite may leave for a scratch database."""
    for suffix in SIDECAR_SUFFIXES:
        Path(str(path) + suffix).unlink(missing_ok=True)


def _seal_snapshot(path: Path) -> None:
    """Prove one staged database carries all its committed data in the main file."""
    wal = Path(str(path) + "-wal")
    try:
        if wal.is_file() and wal.stat().st_size > 0:
            raise BackupError("backup_write_failed", "the database snapshot still has a WAL sidecar")
    except OSError as error:
        raise BackupError("backup_write_failed", "the database snapshot cannot be inspected") from error
    _drop_sidecars(path)


def _exclusion_reason(relative: str, name: str, backup_relative: str | None) -> str | None:
    """Why one owned path is deliberately left out of a snapshot, if it is."""
    parts = PurePosixPath(relative).parts
    if backup_relative is not None and (relative == backup_relative
                                        or relative.startswith(backup_relative + "/")):
        return "backup_directory"
    if any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
        return "transient_state"
    if len(parts) == 1 and name.casefold() in DERIVED_ROOT_FILES:
        return "derived_search_index"
    if name.casefold().endswith(SIDECAR_SUFFIXES):
        return "sqlite_sidecar"
    if name.endswith(".tmp") or (name.startswith(".") and name.endswith(".tmp")):
        return "in_flight_temporary"
    if relative in CREDENTIAL_FILES:
        return "credential"
    if name.casefold().endswith(CREDENTIAL_SUFFIXES):
        return "credential"
    return None


def _scan_installation(data_root: Path, *,
                       backup_directory: Path | None) -> tuple[tuple[SourceFile, ...], tuple[dict, ...]]:
    """Enumerate every file the installation owns, failing closed on links."""
    root = Path(data_root)
    backup_relative = None
    if backup_directory is not None:
        try:
            backup_relative = Path(backup_directory).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            backup_relative = None
    sources: list[SourceFile] = []
    excluded: list[dict] = []

    def consider(path: Path, relative: str) -> None:
        reason = _exclusion_reason(relative, path.name, backup_relative)
        if reason is not None:
            excluded.append({"path": relative, "reason": reason})
            return
        if _is_link(path):
            raise BackupError("backup_source_linked", f"the data root contains a link at {relative}")
        try:
            info = path.stat()
        except OSError as error:
            raise BackupError("backup_source_unreadable", f"{relative} cannot be read") from error
        if not stat.S_ISREG(info.st_mode):
            raise BackupError("backup_source_unreadable", f"{relative} is not a regular file")
        if info.st_size > MAX_ARTIFACT_BYTES:
            raise BackupError("backup_snapshot_size_invalid", f"{relative} exceeds the artifact size budget")
        sources.append(SourceFile(path=path, relative=relative, kind=_kind_for(relative),
                                  byte_size=info.st_size, modified_ns=info.st_mtime_ns))

    for name in OWNED_ROOT_FILES:
        path = root / name
        if path.exists() or _is_link(path):
            consider(path, name)
    for name in DERIVED_ROOT_FILES:
        path = root / name
        if path.exists() or _is_link(path):
            # Derived state is deliberately left out; record it so the manifest
            # shows why the installation snapshot is not byte-for-byte the root.
            excluded.append({"path": name, "reason": "derived_search_index"})
    for name in OWNED_ROOT_DIRECTORIES:
        base = root / name
        if not base.exists() and not _is_link(base):
            continue
        if _is_link(base):
            raise BackupError("backup_source_linked", f"the data root contains a link at {name}")
        if not base.is_dir():
            raise BackupError("backup_source_unreadable", f"{name} is not a directory")
        for current, directories, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            for directory_name in sorted(directories):
                candidate = current_path / directory_name
                relative = candidate.relative_to(root).as_posix()
                if directory_name.casefold() in EXCLUDED_DIRECTORY_NAMES:
                    # Transient/cache subtrees are skipped as a whole, never walked.
                    directories.remove(directory_name)
                    excluded.append({"path": relative, "reason": "transient_state"})
                    continue
                if _is_link(candidate):
                    raise BackupError("backup_source_linked", f"the data root contains a link at {relative}")
            for file_name in sorted(files):
                candidate = current_path / file_name
                consider(candidate, candidate.relative_to(root).as_posix())
    sources.sort(key=lambda item: item.relative)
    excluded.sort(key=lambda item: item["path"])
    return tuple(sources), tuple(excluded)


def _source_fingerprint(sources: tuple[SourceFile, ...]) -> str:
    """A cheap identity of the packaged inputs, used to keep repeated snapshots stable."""
    material = [[item.relative, item.kind, item.byte_size, item.modified_ns] for item in sources]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _unmanaged_roots(data_root: Path, *, backup_directory: Path | None) -> tuple[str, ...]:
    """Top-level entries this version does not package, kept for the operator."""
    root = Path(data_root)
    owned = set(_current_roots()) | set(DERIVED_ROOT_FILES) | {PENDING_FILENAME, DISPLACED_NAME, BACKUP_DIRECTORY}
    if backup_directory is not None:
        try:
            relative = Path(backup_directory).resolve().relative_to(root.resolve())
            if len(relative.parts) == 1:
                owned.add(relative.parts[0])
        except (OSError, ValueError):
            pass

    def managed(name: str) -> bool:
        if name in owned or name.startswith(".") or name.endswith(".replaced"):
            return True
        # WAL/journal sidecars belong to an owned database, not to the operator.
        for entry in OWNED_ROOT_FILES:
            if name.startswith(entry + "-") or name.startswith(entry + "."):
                return True
        return False

    try:
        entries = sorted(path.name for path in root.iterdir())
    except OSError:
        return ()
    return tuple(name for name in entries if not managed(name))


def _sweep_stale_scratch(directory: Path) -> None:
    """Remove our own interrupted snapshot scratch directories, bounded by age.

    Both shapes are matched (review P3-2): the dot-prefixed scratch directory a
    create writes into, and the displaced ``.bundle.old-*`` directory left behind
    when a snapshot id is replaced. A crash between those two renames used to leave
    a directory nothing would ever clean.
    """
    import time
    cutoff = time.time() - STALE_TEMPORARY_SECONDS
    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if not (name.startswith(".companion-v2-") or name.startswith("companion-v2-")):
            continue
        if ".tmp" not in name and ".old-" not in name:
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            if entry.is_dir() and not _is_link(entry):
                _remove_tree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


class InstallationBackup:
    """Snapshot and restore one V2 installation's complete owned state."""

    def __init__(self, runtime, *, now=None, retention_count=10, retention_days=90,
                 directory: Path | None = None):
        self.runtime = runtime
        self.now = now
        self.retention_count = retention_count
        self.retention_days = retention_days
        self._directory = Path(directory) if directory is not None else None

    # -- paths -----------------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._directory or Path(getattr(self.runtime, "backup_directory", None)
                                       or self.runtime.data_root / BACKUP_DIRECTORY)

    @property
    def pending_path(self) -> Path:
        return self.runtime.data_root / PENDING_FILENAME

    def _at(self) -> datetime:
        value = self.now() if self.now is not None else datetime.now(timezone.utc)
        _stamp(value)
        return value

    def _schema_version(self) -> str:
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", ("schema_version",))
        if row is None:
            raise BackupError("backup_metadata_missing", "this data root has no schema version")
        return row["value"]

    # -- snapshot --------------------------------------------------------

    def create(self, *, note: str = "") -> Outcome:
        """Write one verified bundle and return its manifest."""
        try:
            document = self._create(note=note)
            try:
                cleanup = cleanup_backups(self.runtime.data_root, retention_count=self.retention_count,
                                          retention_days=self.retention_days, now=self._at(),
                                          backup_directory=self.directory,
                                          installation_id=self.runtime.installation_id)
            except (OSError, ValueError):
                cleanup = {"removed_count": 0, "removed_bytes": 0, "failed": True}
            return Outcome.success({**document, "retention": cleanup})
        except BackupError as error:
            return Outcome.failed(error.code, str(error))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except (OSError, sqlite3.Error) as error:
            return Outcome.failed("backup_write_failed", str(error))

    def _create(self, *, note: str) -> dict:
        if type(note) is not str or len(note) > 200:
            raise BackupError("backup_note_invalid", "a backup note is at most 200 characters")
        at = self._at()
        self.directory.mkdir(parents=True, exist_ok=True)
        _sweep_stale_scratch(self.directory)
        snapshot_id = digest([self.runtime.installation_id, _stamp(at), note])[:32]
        sidecar, bundle, _payload = _snapshot_paths(self.directory, snapshot_id)
        sources, excluded = _scan_installation(self.runtime.data_root, backup_directory=self.directory)
        fingerprint = _source_fingerprint(sources)
        if sidecar.is_file() and not _is_link(sidecar) and bundle.is_dir():
            existing = self._existing_snapshot(sidecar, fingerprint=fingerprint)
            if existing is not None:
                return existing
        temporary = self.directory / f".companion-v2-{snapshot_id}.{uuid4().hex[:12]}.tmp"
        temporary.mkdir(parents=True)
        try:
            document = self._write_bundle(temporary / PAYLOAD_DIRECTORY, scratch=temporary / "databases",
                                          sources=sources, excluded=excluded,
                                          snapshot_id=snapshot_id, at=at, note=note,
                                          fingerprint=fingerprint)
            artifacts = _validated_artifacts(document)
            _verify_payload(temporary / PAYLOAD_DIRECTORY, artifacts,
                            installation_id=self.runtime.installation_id,
                            schema_version=document["schema_version"])
            displaced = None
            if bundle.exists():
                # The dot prefix keeps the temporary name inside the "managed" set
                # (`_unmanaged_roots`) and inside the stale-scratch sweep.
                displaced = bundle.with_name("." + bundle.name + ".old-" + uuid4().hex[:8])
                os.replace(bundle, displaced)
            os.replace(temporary, bundle)
            # The sidecar is written last: its presence marks the snapshot complete.
            _write_json_atomic(sidecar, document)
            if displaced is not None:
                try:
                    _remove_tree(displaced)
                except OSError:
                    pass
            return document
        finally:
            if temporary.exists():
                _remove_tree(temporary)

    def _existing_snapshot(self, sidecar: Path, *, fingerprint: str) -> dict | None:
        """Reuse an identical snapshot instead of rewriting the same bytes."""
        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (type(document) is not dict or document.get("format") != BACKUP_FORMAT
                or document.get("installation_id") != self.runtime.installation_id
                or document.get("source_fingerprint") != fingerprint
                or not _bundle_complete(self.directory, document)):
            return None
        try:
            if document.get("content_hash") != _canonical_hash(document, _validated_artifacts(document)):
                return None
        except BackupError:
            return None
        return document

    def _write_bundle(self, payload: Path, *, scratch: Path, sources: tuple[SourceFile, ...],
                      excluded: tuple[dict, ...], snapshot_id: str, at: datetime, note: str,
                      fingerprint: str) -> dict:
        """Write every artifact. The payload only ever receives finished files.

        Databases are snapshotted into a scratch directory first: SQLite keeps
        transient WAL-index/temporary files next to the file it is writing, and the
        payload must contain exactly the declared artifacts, so those files are
        created and removed outside the payload tree.

        The live read snapshot is held for **one** thing: the online backup of the
        main database. Everything else (auxiliary databases through their own
        connections, images, novels, JSON configuration) is copied after it is
        released. Holding the runtime lock across the whole payload turned a backup
        into a multi-second freeze of every reader in the process (review P1-1).
        """
        artifacts: list[dict] = []
        redactions: list[dict] = []
        payload.mkdir(parents=True, exist_ok=True)
        scratch.mkdir(parents=True, exist_ok=True)
        main_source = next((item for item in sources if item.relative == MAIN_DATABASE), None)
        if main_source is None:
            raise BackupError("backup_metadata_missing", "this data root has no main database to snapshot")
        staged_main = scratch / "main.sqlite3"
        main_kind = main_source.kind
        try:
            # Phase 1: the only artifact that needs the shared connection and its lock.
            with self.runtime.read_snapshot() as connection:
                schema_version = _schema_version_in(connection)
                try:
                    _snapshot_database(main_source.path, staged_main, connection=connection)
                except _NotSQLiteDatabase:
                    _copy_verified(main_source.path, staged_main)
                    main_kind = "file"
        except BaseException:
            _drop_sidecars(staged_main)
            staged_main.unlink(missing_ok=True)
            raise
        if main_kind == "sqlite":
            _seal_snapshot(staged_main)
        # Phase 2: no runtime lock from here on.
        for index, source in enumerate(sources):
            target = payload.joinpath(*_parts(source.relative))
            kind = source.kind
            removed = 0
            if source.relative == MAIN_DATABASE:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_main, target)
                kind = main_kind
            elif kind == "sqlite":
                staged = scratch / f"{index:05d}.sqlite3"
                try:
                    try:
                        _snapshot_database(source.path, staged)
                    except _NotSQLiteDatabase:
                        _copy_verified(source.path, staged)
                        kind = "file"
                    _seal_snapshot(staged)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
                finally:
                    _drop_sidecars(staged)
                    staged.unlink(missing_ok=True)
            elif _is_json_document(source.relative) and not _is_verbatim(source.relative):
                removed = _redact_json(source.path, target, relative=source.relative)
            else:
                _copy_verified(source.path, target)
            if not target.is_file():
                raise BackupError("backup_write_failed", f"{source.relative} was not written")
            artifacts.append({"path": source.relative, "kind": kind,
                              "bytes": target.stat().st_size, "sha256": _sha256(target)})
            if removed:
                redactions.append({"path": source.relative, "removed_keys": removed})
        _drop_sidecars(staged_main)
        staged_main.unlink(missing_ok=True)
        try:
            scratch.rmdir()
        except OSError:
            pass
        if not any(item["path"] == MAIN_DATABASE for item in artifacts):
            raise BackupError("backup_metadata_missing", "this data root has no main database to snapshot")
        document = {
            "format": BACKUP_FORMAT,
            "snapshot_id": snapshot_id,
            "created_at": _stamp(at),
            "installation_id": self.runtime.installation_id,
            "schema_version": schema_version,
            "byte_size": sum(item["bytes"] for item in artifacts),
            "note": note,
            "artifacts": artifacts,
            "excluded": list(excluded),
            "redactions": redactions,
            "policy": _policy_document(),
            "unmanaged_roots": list(_unmanaged_roots(self.runtime.data_root, backup_directory=self.directory)),
            "source_fingerprint": fingerprint,
            "artifact_count": len(artifacts),
        }
        document["content_hash"] = _canonical_hash(document, artifacts)
        return document

    def listing(self) -> Outcome:
        """List completed snapshots, newest first, in both supported formats.

        Each entry is a summary (``LISTING_FIELDS`` plus ``restorable`` and
        ``restore_scope``); the full manifest belongs to ``inspect``. Structural
        damage is reported separately in ``problems`` so a broken pair is visible
        instead of silently absent, without a per-artifact scan on every page load.
        """
        try:
            if not self.directory.is_dir():
                return Outcome.success({"snapshots": [], "problems": []})
            documents = []
            for sidecar in sorted(self.directory.glob("companion-v2-*.json")):
                if _is_link(sidecar):
                    continue
                try:
                    document = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if type(document) is not dict:
                    continue
                if document.get("format") == LEGACY_BACKUP_FORMAT:
                    if _legacy_listable(self.directory, document):
                        documents.append(_listing_document(
                            document, restorable=True, restore_scope=SCOPE_MAIN_DATABASE))
                elif document.get("format") == BACKUP_FORMAT and _bundle_listable(self.directory, document):
                    documents.append(_listing_document(
                        document, restorable=True, restore_scope=SCOPE_COMPLETE))
            documents.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return Outcome.success({"snapshots": documents,
                                    "problems": list(_listing_problems(self.directory))})
        except BackupError as error:
            return Outcome.failed(error.code, str(error))

    def inspect(self, *, snapshot_id: str) -> Outcome:
        """Verify one named snapshot without restoring it.

        This is the full check: every artifact's size and digest, every database's
        integrity, the payload's exact file set. A listing deliberately does not do
        this, so anything that acts on a snapshot starts here.
        """
        try:
            document = _read_snapshot_manifest(self.directory, snapshot_id)
            if document["format"] == LEGACY_BACKUP_FORMAT:
                details = _verify_legacy(document, directory=self.directory,
                                         installation_id=self.runtime.installation_id,
                                         schema_version=self._schema_version())
            else:
                details = _verify_bundle(document, directory=self.directory,
                                         installation_id=self.runtime.installation_id,
                                         schema_version=self._schema_version())
            return Outcome.success({"manifest": document, "verified": details,
                                    "restorable": details["restorable"],
                                    "restore_scope": details["restore_scope"],
                                    "warnings": list(details.get("warnings", ()))})
        except BackupError as error:
            return Outcome.failed(error.code, str(error))
        except (OSError, ValueError) as error:
            return Outcome.failed("backup_snapshot_unreadable", str(error))

    # -- restore ---------------------------------------------------------

    def _credential_preview(self, manifest: dict) -> dict:
        """What this restore will *not* forget, so the operator can see it first."""
        keys = []
        redactions = manifest.get("redactions")
        for entry in redactions if isinstance(redactions, list) else ():
            if type(entry) is not dict or type(entry.get("path")) is not str:
                continue
            live = _live_path(self.runtime.data_root, entry["path"])
            try:
                document = json.loads(live.read_bytes())
            except (OSError, ValueError, UnicodeError):
                continue
            found = sorted(".".join(path) for path, _value in _secret_paths(document))
            if found:
                keys.append({"path": entry["path"], "keys": found})
        return {"policy": "preserved",
                "files": _current_credential_files(self.runtime.data_root),
                "keys": keys}

    def preview_restore(self, *, snapshot_id: str) -> Outcome:
        """Verify a snapshot and report what a restore would replace."""
        verified = self.inspect(snapshot_id=snapshot_id)
        if not verified.is_success:
            return verified
        try:
            counts = {}
            with self.runtime.read_only_snapshot() as connection:
                for table in COUNTED_TABLES:
                    row = connection.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
                    counts[table] = row["n"] if row is not None else 0
            details = verified.unwrap()
            return Outcome.success({"snapshot_id": snapshot_id, "current": counts,
                                    "verified": {**details["verified"], "manifest": details["manifest"]},
                                    "restorable": details["restorable"],
                                    "restore_scope": details["restore_scope"],
                                    "warnings": details["warnings"],
                                    "preserves": list(details["verified"].get("preserves", ())),
                                    "credentials": self._credential_preview(details["manifest"]),
                                    "effect": "requires_restart"})
        except sqlite3.Error as error:
            return Outcome.failed("backup_preview_failed", str(error))

    def restore(self, *, snapshot_id: str, confirmed_hash: str, confirmed_scope: str = "") -> Outcome:
        """Stage a restore. The host must restart before it takes effect.

        ``confirmed_scope`` carries the scope the operator saw in the preview. A v1
        snapshot is a main-database-only restore and is refused without it, so no
        caller can quietly turn a partial restore into a "complete" one.
        """
        try:
            verified = self.inspect(snapshot_id=snapshot_id)
            if not verified.is_success:
                return verified
            details = verified.unwrap()
            manifest = details["manifest"]
            scope = details["restore_scope"]
            if scope == SCOPE_MAIN_DATABASE:
                if confirmed_scope != SCOPE_MAIN_DATABASE:
                    raise BackupError(
                        "backup_legacy_confirmation_required",
                        "this snapshot was written by the single-database format and only restores "
                        "companion-v2.sqlite3; confirm the main_database_only scope to proceed")
            elif confirmed_scope not in ("", SCOPE_COMPLETE):
                raise BackupError("backup_restore_scope_invalid",
                                  "this snapshot is a whole-installation bundle, not a partial restore")
            # The operator confirms the exact bytes they were shown. A snapshot that
            # changed between preview and confirmation is refused rather than applied.
            if type(confirmed_hash) is not str or confirmed_hash != manifest["content_hash"]:
                raise BackupError("backup_confirmation_mismatch", "the confirmed digest does not match this snapshot")
            if self.pending_path.exists():
                raise BackupError("backup_restore_pending",
                                  "a restore is already waiting for a restart; restart the plugin to finish "
                                  "it, or cancel it from the backup page")
            at = _stamp(self._at())
            # Keep the data being replaced. A failed restart is otherwise unrecoverable.
            safety = self._create(note=f"pre-restore safety copy of {snapshot_id}")
            document = {
                "format": RESTORE_FORMAT,
                "snapshot_id": snapshot_id,
                "content_hash": manifest["content_hash"],
                "installation_id": self.runtime.installation_id,
                "safety_snapshot_id": safety["snapshot_id"],
                "requested_at": at,
                "restore_scope": scope,
            }
            _write_json_atomic(self.pending_path, document)
            return Outcome.success({"snapshot_id": snapshot_id, "staged_at": at,
                                    "safety_snapshot_id": safety["snapshot_id"],
                                    "restore_scope": scope,
                                    "warnings": details["warnings"],
                                    "preserves": list(details["verified"].get("preserves", ())),
                                    "artifact_count": manifest.get("artifact_count"),
                                    "effect": "restart_required",
                                    "note": "the data root is unchanged until the host restarts"})
        except BackupError as error:
            return Outcome.failed(error.code, str(error))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except (OSError, sqlite3.Error) as error:
            return Outcome.failed("backup_restore_failed", str(error))

    def quarantine(self, *, snapshot_id: str, confirm: bool = False) -> Outcome:
        """Move one incomplete pair out of the active directory, keeping every byte.

        An interrupted bundle cannot be restored, but it does block the only
        recovery path the operator has: a backup-directory move refuses a set that
        contains one forever. Quarantining is the narrowest fix: the pair goes to
        ``.incomplete/`` beside it, nothing is deleted, and a snapshot a staged
        restore still needs is refused rather than moved.
        """
        try:
            if confirm is not True:
                raise BackupError("backup_quarantine_confirmation_required",
                                  "moving an incomplete snapshot aside requires an explicit confirmation")
            from .management import quarantine_incomplete_snapshots
            result = quarantine_incomplete_snapshots(self.directory, snapshot_id=snapshot_id,
                                                     data_root=self.runtime.data_root)
            return Outcome.success(result)
        except BackupError as error:
            return Outcome.failed(error.code, str(error))
        except (ValueError, OSError) as error:
            return Outcome.failed("backup_quarantine_failed", str(error))

    def pending(self) -> Outcome:
        """Report a staged restore, including why it cannot be applied.

        A request this version cannot apply still blocks new restores, so the report
        has to name the problem and say that it can be cancelled. It never fails the
        read: a page that cannot see the problem cannot offer the way out.
        """
        try:
            if not self.pending_path.is_file():
                return Outcome.success({"pending": None, "restorable": False,
                                        "cancellable": False, "problem": None})
            try:
                document = json.loads(self.pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                return Outcome.success({"pending": None, "restorable": False, "cancellable": True,
                                        "problem": {"code": "backup_pending_unreadable",
                                                    "detail": str(error)}})
            if type(document) is not dict:
                return Outcome.success({"pending": None, "restorable": False, "cancellable": True,
                                        "problem": {"code": "backup_pending_invalid",
                                                    "detail": "the staged restore request is malformed"}})
            if document.get("format") == LEGACY_BACKUP_FORMAT:
                return Outcome.success({"pending": document, "restorable": False, "cancellable": True,
                                        "problem": {"code": "backup_pending_legacy",
                                                    "detail": "written by the single-database format; "
                                                              "cancel it and stage the snapshot again"}})
            if (document.get("format") != RESTORE_FORMAT or type(document.get("snapshot_id")) is not str
                    or type(document.get("content_hash")) is not str):
                return Outcome.success({"pending": document, "restorable": False, "cancellable": True,
                                        "problem": {"code": "backup_pending_invalid",
                                                    "detail": "the staged restore request is malformed"}})
            scope = document.get("restore_scope", SCOPE_COMPLETE)
            return Outcome.success({"pending": document, "restorable": True, "cancellable": True,
                                    "restore_scope": scope, "problem": None})
        except OSError as error:
            return Outcome.failed("backup_pending_unreadable", str(error))

    def cancel_pending(self, *, confirm: bool = False, reason: str = "") -> Outcome:
        """Abandon a staged restore request without touching installation data.

        This is the way out of a request that can never be applied (legacy format,
        unreadable file, a snapshot on an unplugged disk). It is deliberately narrow:
        an explicit confirmation, a best-effort safety snapshot of the current state,
        and it never deletes a snapshot — only the request file, and the scratch
        directory of a restore that already committed. A journal that still has to be
        rolled back is left alone: that needs a restart, not a cancel.
        """
        try:
            if confirm is not True:
                raise BackupError("backup_cancel_confirmation_required",
                                  "abandoning a staged restore requires an explicit confirmation")
            if not self.pending_path.is_file():
                return Outcome.success({"cancelled": False, "reason": "no_pending_restore"})
            raw: dict = {}
            try:
                parsed = json.loads(self.pending_path.read_text(encoding="utf-8"))
                raw = parsed if type(parsed) is dict else {}
            except (OSError, ValueError):
                raw = {}
            code = None
            try:
                request = _read_pending_request(self.pending_path)
            except BackupError as error:
                request, code = raw, error.code
            safety_id = None
            safety_failure = None
            try:
                label = raw.get("snapshot_id") if type(raw.get("snapshot_id")) is str else "an unreadable request"
                safety_id = self._create(note=f"pre-cancel safety copy of {label}")["snapshot_id"]
            except (BackupError, TaskError, OSError, sqlite3.Error) as error:
                safety_failure = getattr(error, "code", type(error).__name__)
            finalized = []
            for journal in _journals(self.runtime.data_root):
                document = _read_journal(journal)
                if not _journal_matches(document, request):
                    continue
                if document.get("phase") != "committed":
                    raise BackupError(
                        "backup_restore_journal_pending",
                        "a previous restore attempt is still journaled; restart the plugin so it can be "
                        "rolled back, then cancel the request")
                try:
                    _publish_displaced(journal, self.runtime.data_root)
                except OSError as error:
                    raise BackupError("backup_cancel_failed",
                                      f"the committed restore could not be finalized: {error}") from error
                _discard_journal(journal)
                finalized.append(journal.root.name)
            self.pending_path.unlink()
            return Outcome.success({
                "cancelled": True,
                "abandoned": {"format": raw.get("format"), "snapshot_id": raw.get("snapshot_id"),
                              "requested_at": raw.get("requested_at"),
                              "safety_snapshot_id": raw.get("safety_snapshot_id"),
                              "code": code, "reason": reason},
                "safety_snapshot_id": safety_id, "safety_failure": safety_failure,
                "journals_finalized": finalized,
                "snapshots_deleted": [],
            })
        except BackupError as error:
            return Outcome.failed(error.code, str(error))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except OSError as error:
            return Outcome.failed("backup_cancel_failed", str(error))


# -- applying a staged restore ----------------------------------------------


def current_schema_version(data_root: Path) -> str | None:
    """Read a data root's stored schema revision without opening it for writing.

    Restore runs before ``StorageRuntime.open``, so it cannot ask a runtime. It
    also must not open the database read-write: that would create the file and run
    migrations against the very data root about to be replaced.
    """
    database = Path(data_root) / MAIN_DATABASE
    if not database.is_file():
        return None
    try:
        connection = _live_connection(database)
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'").fetchone()
        return row["value"] if row is not None else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def apply_pending(data_root: Path, *, installation_id: str, schema_version: str,
                  backup_directory: Path | None = None) -> dict:
    """Swap in a staged bundle. Called by the host before the store is opened.

    This runs with no live connection, which is the only moment the installation
    files may be replaced safely. Errors are returned rather than raised so a failed
    restore leaves the caller with the original installation and a readable reason.
    """
    try:
        return _apply_pending(data_root, installation_id=installation_id, schema_version=schema_version,
                              backup_directory=backup_directory)
    except BackupError as error:
        return {"applied": False, "reason": error.code, "detail": str(error)}
    except TaskError as error:
        return {"applied": False, "reason": error.code, "detail": str(error)}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        return {"applied": False, "reason": "backup_restore_failed",
                "detail": f"{type(error).__name__}: {error}"}
    except Exception as error:  # start-up must survive an unforeseen failure
        return {"applied": False, "reason": "backup_restore_failed",
                "detail": f"{type(error).__name__}: {error}"}


def _journals(data_root: Path) -> tuple[_Journal, ...]:
    try:
        entries = sorted(Path(data_root).glob(JOURNAL_PREFIX + "*"))
    except OSError:
        return ()
    return tuple(_Journal(entry) for entry in entries
                 if entry.is_dir() and not _is_link(entry))


def _read_journal(journal: _Journal) -> dict | None:
    if _is_link(journal.document_path):
        return None
    try:
        document = json.loads(journal.document_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if type(document) is not dict or document.get("format") != JOURNAL_FORMAT:
        return None
    return document


def _journal_matches(document: dict | None, request: dict) -> bool:
    """True when a journal belongs to this request.

    A cancel may not know the confirmed digest (an unreadable request has none), so
    a missing digest matches on the snapshot id alone; an apply always has one.
    """
    if not document or document.get("snapshot_id") != request.get("snapshot_id"):
        return False
    expected = request.get("content_hash")
    return expected is None or document.get("content_hash") == expected


def _apply_pending(data_root: Path, *, installation_id: str, schema_version: str,
                   backup_directory: Path | None = None) -> dict:
    root = Path(data_root)
    pending_path = root / PENDING_FILENAME
    if not pending_path.is_file():
        return {"applied": False, "reason": "no_pending_restore"}
    request = _read_pending_request(pending_path)
    if request.get("installation_id") not in (None, installation_id):
        raise BackupError("backup_pending_invalid", "the staged request belongs to another installation")
    directory = Path(backup_directory) if backup_directory is not None else root / BACKUP_DIRECTORY
    for journal in _journals(root):
        document = _read_journal(journal)
        if _journal_matches(document, request):
            if document.get("phase") == "committed":
                # A previous attempt already swapped and verified this snapshot;
                # finish the cleanup instead of touching the installation again.
                return _finalize(journal, root, pending_path, request)
            _recover_journal(journal, root)
            _discard_journal(journal)
            continue
        if document is not None and document.get("phase") == "committed":
            # The installation is already committed; only the scratch copy is left.
            _discard_journal(journal)
            continue
        _recover_journal(journal, root)
        _discard_journal(journal)
    document = _read_snapshot_manifest(directory, request["snapshot_id"])
    if document["format"] == LEGACY_BACKUP_FORMAT:
        if request.get("restore_scope") != SCOPE_MAIN_DATABASE:
            raise BackupError("backup_pending_invalid",
                              "the staged request does not declare the main-database-only scope")
        return _apply_legacy(root, directory=directory, document=document, request=request,
                             pending_path=pending_path, installation_id=installation_id,
                             schema_version=schema_version)
    if request.get("restore_scope") not in (None, SCOPE_COMPLETE):
        raise BackupError("backup_pending_invalid",
                          "the staged request declares a partial scope for a whole-installation snapshot")
    verified = _verify_bundle(document, directory=directory, installation_id=installation_id,
                              schema_version=schema_version)
    if document["content_hash"] != request["content_hash"]:
        raise BackupError("backup_restore_unverified", "the staged snapshot no longer verifies")
    return _stage_and_swap(root, directory=directory, document=document, verified=verified,
                           request=request, pending_path=pending_path)


def _apply_legacy(data_root: Path, *, directory: Path, document: dict, request: dict,
                  pending_path: Path, installation_id: str, schema_version: str) -> dict:
    """Restore a 7.0.7 single-database snapshot: the main database only.

    This is the capability 7.0.7 had. It is journaled exactly like a whole-installation
    restore, so a failure still rolls back to the original bytes, but it deliberately
    prunes nothing under the owned roots: the snapshot never carried those stores, and
    deleting them would turn "restore my old library" into "lose today's images".
    Only the derived search index is dropped, because it answers for rows that the
    restored database no longer has. The result says all of that in ``preserves``.
    """
    snapshot_id = _valid_snapshot_id(document.get("snapshot_id"))
    verified = _verify_legacy(document, directory=directory, installation_id=installation_id,
                              schema_version=schema_version)
    if document.get("content_hash") != request.get("content_hash"):
        raise BackupError("backup_restore_unverified", "the staged snapshot no longer verifies")
    source = _legacy_database_path(directory, snapshot_id)
    artifacts = verified["artifacts"]
    journal = _Journal(data_root / f"{JOURNAL_PREFIX}{snapshot_id[:8]}-{uuid4().hex[:8]}")
    journal.root.mkdir(parents=True)
    try:
        _ensure_space(data_root, verified["byte_size"])
        _copy_verified(source, journal.stage_root.joinpath(*_parts(MAIN_DATABASE)),
                       expected_bytes=verified["byte_size"], expected_hash=document["content_hash"])
        present, absent = _prepare_live_paths(data_root, artifacts)
        journal_document = {
            "format": JOURNAL_FORMAT,
            "phase": "prepared",
            "restore_scope": SCOPE_MAIN_DATABASE,
            "snapshot_id": snapshot_id,
            "content_hash": document["content_hash"],
            "installation_id": installation_id,
            "artifacts": [{"path": MAIN_DATABASE, "kind": "sqlite"}],
            "roots": [MAIN_DATABASE],
            "derived": list(DERIVED_ROOT_FILES),
            "present": sorted(present),
            "absent": sorted(absent),
            "credential_merges": [],
            "credential_files": [],
        }
        _write_json_atomic(journal.document_path, journal_document)
        _swap_artifacts(journal, data_root, artifacts)
        _prune_derived(journal, data_root, journal_document["derived"])
        _verify_installation(data_root, artifacts, installation_id=installation_id,
                             schema_version=schema_version)
        _write_json_atomic(journal.document_path, {**journal_document, "phase": "committed"})
        return _finalize(journal, data_root, pending_path, request)
    except Exception as error:
        return _restore_failure(journal, data_root, error)


def _restore_failure(journal: _Journal, data_root: Path, error: BaseException) -> dict:
    """Roll back a failed restore and describe what happened, never raising.

    Both restore paths share this: a failure before the commit marker must put the
    original bytes back, and if even that fails the host has to know the data root
    needs manual review instead of being told "nothing happened".
    """
    reason = error.code if isinstance(error, BackupError) else "backup_restore_failed"
    detail = str(error) if isinstance(error, (BackupError, OSError, sqlite3.Error)) \
        else f"{type(error).__name__}: {error}"
    try:
        _recover_journal(journal, data_root)
        _discard_journal(journal)
    except Exception as rollback_error:
        return {"applied": False, "reason": "backup_restore_rollback_incomplete",
                "detail": f"{detail}; rollback: {rollback_error}",
                "journal": journal.root.name}
    return {"applied": False, "reason": reason, "detail": detail, "rolled_back": True}



def _stage_and_swap(data_root: Path, *, directory: Path, document: dict, verified: dict,
                    request: dict, pending_path: Path) -> dict:
    artifacts = verified["artifacts"]
    roots = document["policy"]["roots"]
    journal = _Journal(data_root / f"{JOURNAL_PREFIX}{document['snapshot_id'][:8]}-{uuid4().hex[:8]}")
    journal.root.mkdir(parents=True)
    try:
        _ensure_space(data_root, verified["byte_size"])
        payload = _snapshot_paths(directory, document["snapshot_id"])[2]
        merged: list[dict] = []
        for item in artifacts:
            staged = journal.stage_root.joinpath(*_parts(item["path"]))
            _copy_verified(payload.joinpath(*_parts(item["path"])), staged,
                           expected_bytes=item["bytes"], expected_hash=item["sha256"])
            # The payload was verified above; from here the staged copy may gain the
            # credentials the installation uses today, so its digest is re-recorded
            # and the installation check compares against that (review P2-2).
            if _is_json_document(item["path"]) and not _is_verbatim(item["path"]):
                live = _live_path(data_root, item["path"])
                if live.is_file() and not _is_link(live):
                    keys = _merge_current_credentials(staged, live)
                    if keys:
                        merged.append({"path": item["path"], "keys": keys,
                                       "sha256": _sha256(staged)})
        present, absent = _prepare_live_paths(data_root, artifacts)
        journal_document = {
            "format": JOURNAL_FORMAT,
            "phase": "prepared",
            "restore_scope": SCOPE_COMPLETE,
            "snapshot_id": document["snapshot_id"],
            "content_hash": document["content_hash"],
            "installation_id": document["installation_id"],
            "artifacts": [{"path": item["path"], "kind": item["kind"]} for item in artifacts],
            "roots": list(roots),
            "present": sorted(present),
            "absent": sorted(absent),
            "credential_merges": merged,
            "credential_files": [],
        }
        # From here on the data root is mutated; the journal is the recovery plan.
        _write_json_atomic(journal.document_path, journal_document)
        _swap_artifacts(journal, data_root, artifacts)
        preserved = _prune_installation(journal, data_root, document, artifacts)
        journal_document["credential_files"] = preserved
        _verify_installation(data_root, artifacts, installation_id=document["installation_id"],
                             schema_version=document["schema_version"],
                             overrides={item["path"]: item["sha256"] for item in merged})
        # The commit marker. After it, a crash means "finish the cleanup", never
        # "roll back"; before it, a crash is rolled back on the next start.
        _write_json_atomic(journal.document_path, {**journal_document, "phase": "committed"})
        return _finalize(journal, data_root, pending_path, request)
    except Exception as error:  # roll back before returning any failure to the host
        return _restore_failure(journal, data_root, error)


def _ensure_space(data_root: Path, required: int) -> None:
    try:
        free = shutil.disk_usage(data_root).free
    except OSError:
        return
    if free < required + STAGING_MARGIN_BYTES:
        raise BackupError("backup_restore_space_insufficient",
                          "the data volume does not have room for the staged restore")


def _prepare_live_paths(data_root: Path, artifacts: tuple[dict, ...]) -> tuple[list[str], list[str]]:
    """Check the swap preconditions before anything is mutated."""
    present: list[str] = []
    absent: list[str] = []
    for item in artifacts:
        live = _live_path(data_root, item["path"])
        if _is_link(live):
            raise BackupError("backup_source_linked", f"the installation contains a link at {item['path']}")
        if live.exists():
            if not live.is_file():
                raise BackupError("backup_restore_unverified",
                                  f"the installation path {item['path']} is not a file")
            present.append(item["path"])
        else:
            absent.append(item["path"])
        if item["kind"] == "sqlite":
            for suffix in SIDECAR_SUFFIXES:
                sidecar = Path(str(live) + suffix)
                if _is_link(sidecar):
                    raise BackupError("backup_source_linked",
                                      f"the installation contains a link at {item['path']}{suffix}")
                if sidecar.exists() and not sidecar.is_file():
                    raise BackupError("backup_restore_unverified",
                                      f"the installation sidecar {item['path']}{suffix} is not a file")
    return present, absent


def _swap_artifacts(journal: _Journal, data_root: Path, artifacts: tuple[dict, ...]) -> None:
    # The main database goes first so a crash leaves a complete store, not a
    # half-upgraded set of side databases.
    order = sorted(artifacts, key=lambda item: (item["path"] != MAIN_DATABASE, item["path"]))
    for item in order:
        live = _live_path(data_root, item["path"])
        displaced = journal.rollback_root.joinpath(*_parts(item["path"]))
        if live.exists():
            displaced.parent.mkdir(parents=True, exist_ok=True)
            os.replace(live, displaced)
        if item["kind"] == "sqlite":
            for suffix in SIDECAR_SUFFIXES:
                sidecar = Path(str(live) + suffix)
                if sidecar.exists():
                    target = journal.rollback_root.joinpath(*_parts(item["path"] + suffix))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(sidecar, target)
        live.parent.mkdir(parents=True, exist_ok=True)
        os.replace(journal.stage_root.joinpath(*_parts(item["path"])), live)


def _prune_installation(journal: _Journal, data_root: Path, document: dict,
                        artifacts: tuple[dict, ...]) -> list[str]:
    """Remove post-snapshot state under the roots the snapshot declared.

    A restore reverts the installation, so anything owned today that the snapshot
    did not carry (stale originals, caches, a database created after the backup)
    would otherwise survive as a newer generation next to older authoritative rows.
    Credential stores are the deliberate exception (review P2-2): the snapshot never
    carried them, so deleting them would not "revert" anything — it would silently
    drop a device pairing or a login. They are kept and reported instead. Files
    outside the declared roots are never touched: an unowned file belongs to the
    operator, not to this installation.
    """
    artifact_paths = {item["path"] for item in artifacts}
    preserved: list[str] = []
    for entry in document["policy"]["roots"]:
        base = data_root / entry
        if entry in artifact_paths or not (base.exists() or _is_link(base)):
            continue
        if _is_link(base):
            # A link inside the installation cannot be pruned without either
            # following it out of the data root or deleting the link target.
            raise BackupError("backup_source_linked",
                              f"the installation path {entry} is a link; remove it before restoring")
        if entry in OWNED_ROOT_FILES or base.is_file():
            _prune_path(journal, data_root, base)
            continue
        stack = [base]
        empty_directories: list[Path] = []
        while stack:
            current = stack.pop()
            for child in sorted(current.iterdir()):
                relative = child.relative_to(data_root).as_posix()
                if _is_link(child):
                    raise BackupError("backup_source_linked",
                                      f"the installation path {relative} is a link; remove it before restoring")
                if child.is_file():
                    if relative in artifact_paths:
                        continue
                    if _is_credential_path(relative, child.name):
                        preserved.append(relative)
                        continue
                    _prune_path(journal, data_root, child)
                    continue
                stack.append(child)
                empty_directories.append(child)
        for directory in sorted(empty_directories, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                continue
    _prune_derived(journal, data_root, document["policy"].get("derived", ()))
    return sorted(preserved)


def _prune_derived(journal: _Journal, data_root: Path, names) -> None:
    """Drop derived state: a stale index must not answer for restored rows."""
    for name in names:
        candidates = (data_root / name, *(Path(str(data_root / name) + suffix) for suffix in SIDECAR_SUFFIXES))
        for candidate in candidates:
            if _is_link(candidate):
                raise BackupError("backup_source_linked",
                                  f"the installation path {candidate.name} is a link; remove it before restoring")
            if candidate.exists():
                _prune_path(journal, data_root, candidate)


def _prune_path(journal: _Journal, data_root: Path, path: Path) -> None:
    relative = path.relative_to(data_root)
    target = journal.pruned_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir() and not _is_link(target):
        _remove_tree(target)
    elif target.exists() or _is_link(target):
        target.unlink()
    os.replace(path, target)


def _verify_installation(data_root: Path, artifacts: tuple[dict, ...], *, installation_id: str,
                         schema_version: str, overrides: dict[str, str] | None = None) -> None:
    """Prove every artifact on disk is the byte sequence this restore decided to write.

    ``overrides`` carries the digest of a staged file that deliberately differs from
    the manifest: a restored JSON document that kept the installation's current
    credentials. Those files are compared against the digest recorded when the merge
    happened, so the check stays byte-exact.
    """
    expected_overrides = dict(overrides or {})
    for item in artifacts:
        live = _live_path(data_root, item["path"])
        if _is_link(live) or not live.is_file():
            raise BackupError("backup_restore_unverified", f"the restored artifact {item['path']} is missing")
        override = expected_overrides.get(item["path"])
        if override is None:
            if live.stat().st_size != item["bytes"] or _sha256(live) != item["sha256"]:
                raise BackupError("backup_restore_unverified",
                                  f"the restored artifact {item['path']} does not match the snapshot")
        elif _sha256(live) != override:
            raise BackupError("backup_restore_unverified",
                              f"the restored credential merge for {item['path']} does not match")
    _database_details(data_root / MAIN_DATABASE, installation_id=installation_id,
                      schema_version=schema_version, foreign_keys=True)


def _recover_journal(journal: _Journal, data_root: Path) -> None:
    """Put the installation back exactly as it was before this attempt.

    Recovery is driven by what the journal still holds, not by a phase log, so it
    is idempotent after an interrupted attempt: a displaced original is moved back,
    a file that was placed without a predecessor is removed, and a file that was
    never swapped is left alone.
    """
    document = _read_journal(journal)
    if document is None:
        if _tree_has_files(journal.rollback_root) or _tree_has_files(journal.pruned_root) \
                or _tree_has_files(journal.superseded_root):
            raise BackupError("backup_restore_journal_unreadable",
                              "a restore journal lost its protocol file; the data needs manual review")
        return
    roots = document.get("roots")
    if type(roots) is not list or not roots:
        roots = list(_current_roots())
    problems: list[str] = []
    artifacts = document.get("artifacts")
    if type(artifacts) is not list:
        artifacts = []
    for item in artifacts:
        if type(item) is not dict:
            continue
        try:
            relative = _safe_relative_path(item.get("path"), roots)
        except BackupError:
            continue
        candidates = [(relative, True)]
        if item.get("kind") == "sqlite":
            candidates.extend((relative + suffix, False) for suffix in SIDECAR_SUFFIXES)
        for candidate, is_artifact in candidates:
            live = _live_path(data_root, candidate)
            displaced = journal.rollback_root.joinpath(*_parts(candidate))
            staged = journal.stage_root.joinpath(*_parts(candidate))
            try:
                if displaced.is_file() or _is_link(displaced):
                    # The original was displaced from the live path: put it back.
                    if live.is_file() or _is_link(live):
                        live.unlink()
                elif is_artifact and not (staged.is_file() or _is_link(staged)):
                    # Placed from the stage and no original existed: remove ours.
                    if live.is_file() or _is_link(live):
                        live.unlink()
            except OSError as error:
                problems.append(f"{candidate}: {error}")
    _restore_tree(journal.rollback_root, data_root, first=MAIN_DATABASE)
    _restore_tree(journal.pruned_root, data_root)
    _restore_tree(journal.superseded_root, data_root)
    if problems:
        raise BackupError("backup_restore_rollback_incomplete",
                          "the previous installation could not be fully restored: " + "; ".join(problems[:3]))


def _tree_has_files(root: Path) -> bool:
    if not root.exists():
        return False
    for _current, _directories, files in os.walk(root, followlinks=False):
        if files:
            return True
    return False


def _restore_tree(source_root: Path, data_root: Path, *, first: str | None = None) -> None:
    """Move journaled files back, main database first."""
    if not source_root.is_dir():
        return
    entries: list[Path] = []
    for current, directories, files in os.walk(source_root, followlinks=False):
        current_path = Path(current)
        for name in files:
            entries.append(current_path / name)
        directories[:] = [name for name in directories if not _is_link(current_path / name)]
    entries.sort(key=lambda path: (path.relative_to(source_root).as_posix() != first,
                                   path.relative_to(source_root).as_posix()))
    for path in entries:
        relative = path.relative_to(source_root)
        target = data_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            raise BackupError("backup_restore_rollback_incomplete",
                              f"{relative.as_posix()} is a directory, cannot restore the file")
        os.replace(path, target)


def _discard_journal(journal: _Journal) -> None:
    try:
        _remove_tree(journal.root)
    except OSError:
        pass


def _finalize(journal: _Journal, data_root: Path, pending_path: Path, request: dict) -> dict:
    """Publish the displaced database, clear the pending request, drop the journal."""
    document = _read_journal(journal) or {}
    merges = [entry for entry in document.get("credential_merges") or () if type(entry) is dict]
    result = {"applied": True, "snapshot_id": request["snapshot_id"],
              "safety_snapshot_id": request.get("safety_snapshot_id"),
              "restore_scope": document.get("restore_scope",
                                            request.get("restore_scope", SCOPE_COMPLETE)),
              # What a restore deliberately kept instead of reverting (review P2-2).
              "credentials_preserved": {
                  "files": sorted(document.get("credential_files") or ()),
                  "keys": [{"path": entry.get("path"), "keys": sorted(entry.get("keys") or ())}
                           for entry in merges],
              }}
    if result["restore_scope"] == SCOPE_MAIN_DATABASE:
        result["preserves"] = list(LEGACY_PRESERVES)
        result["warnings"] = list(LEGACY_WARNINGS)
    try:
        _publish_displaced(journal, data_root)
    except OSError as error:
        # The installation is already committed and verified; keep the journal and
        # the pending request so the next start can finish the publication.
        result["pending_finalization_required"] = True
        result["journal_cleanup_required"] = True
        result["detail"] = str(error)
        return result
    if (Path(data_root) / DISPLACED_NAME).is_file():
        result["replaced_kept_at"] = DISPLACED_NAME
    try:
        pending_path.unlink()
    except OSError as error:
        result["pending_finalization_required"] = True
        result["detail"] = str(error)
        return result
    try:
        _remove_tree(journal.root)
    except OSError:
        result["journal_cleanup_required"] = True
    return result


def _publish_displaced(journal: _Journal, data_root: Path) -> None:
    """Keep the previous generation next to the installation as ``.replaced``.

    Idempotent by construction (review P2-6): only a journal that still holds the
    displaced main database publishes anything. A retried finalize finds the
    publication already done and must leave ``.replaced`` exactly where it is —
    moving it into the journal that is about to be deleted would lose the one copy
    the operator can roll back to. Sidecars are published before the main file, so
    seeing ``.replaced`` means its whole generation is there.
    """
    source = journal.rollback_root.joinpath(*_parts(MAIN_DATABASE))
    if not source.is_file():
        return
    existing = [data_root / DISPLACED_NAME,
                *(Path(str(data_root / DISPLACED_NAME) + suffix) for suffix in SIDECAR_SUFFIXES)]
    for path in existing:
        if path.is_file() or _is_link(path):
            target = journal.superseded_root / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            os.replace(path, target)
    for suffix in (*SIDECAR_SUFFIXES, ""):
        candidate = journal.rollback_root.joinpath(*_parts(MAIN_DATABASE + suffix))
        if candidate.is_file():
            os.replace(candidate, data_root / (DISPLACED_NAME + suffix))
