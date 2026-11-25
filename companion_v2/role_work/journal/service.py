"""Trusted owner journal storage; these methods grant no audience permission.

Only current, unrevoked inputs are available to consumers. Subjective writing is
never published as a factual SourceRecord or written to a relationship account.
"""

from datetime import datetime, timezone
import json
from threading import get_ident
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import OperationRef, RoleOwnerRef, VersionRef, normalize_utc
from companion_v2.role_work.ledger import PersistentRoleLifeLedger
from companion_v2.sources.dependencies import (
    active_validation_batch,
    is_unguarded_checked,
    validation_batch,
)
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from . import codec
from .models import (DerivativeRecord, JournalEntry, JournalHead, JournalKey, JournalKind,
                     JournalReceipt, JournalRef, JournalState, local_day, revision)


class RoleJournalService:
    def __init__(self, runtime, *, now=None):
        self.runtime = runtime
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.operations = OperationRepository(runtime)
        self.sources = SourceRepository(runtime)
        self.life = PersistentRoleLifeLedger(runtime, now=self.now)

    def _time(self, observed_at):
        return normalize_utc(self.now() if observed_at is None else observed_at, field="journal operation time")

    def _owner(self, owner):
        if not isinstance(owner, RoleOwnerRef) or owner.installation_id != self.runtime.installation_id:
            raise TaskError("journal_owner_mismatch", "journal owner belongs to another installation")
        return digest(owner)

    def _read_only_batch(self, at):
        depth = int(getattr(self.runtime, "_write_depth", 0) or 0)
        if depth > 0:
            owner_tid = getattr(self.runtime, "_write_owner_thread_id", None)
            if owner_tid is None or owner_tid == get_ident():
                return None
        return active_validation_batch(self.runtime, at)

    def _row(self, key, *, batch=None):
        if not isinstance(key, JournalKey):
            raise ValueError("journal key must be explicit")
        self._owner(key.owner)
        entry_key = digest(key)
        if batch is not None:
            row_cache = batch.extras.get("role_journal.rows")
            if row_cache is not None and entry_key in row_cache:
                return row_cache[entry_key]
        row = self.runtime.read_one("SELECT * FROM role_journals WHERE entry_key = ?", (entry_key,))
        if batch is not None:
            batch.extras.setdefault("role_journal.rows", {})[entry_key] = row
        return row

    def head(self, key):
        row = self._row(key)
        return codec.head_from_row(row) if row is not None else JournalHead(key, 0, JournalState.ABSENT, None)

    def list_days(self, owner, *, kind, limit=50, after_date=None, include_deleted=False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("journal page size must be between 1 and 100")
        if not isinstance(kind, JournalKind) or type(include_deleted) is not bool:
            raise ValueError("journal archive query requires an explicit kind and deletion filter")
        if after_date is not None:
            local_day(after_date)
        rows = self.runtime.read_all("""SELECT * FROM role_journals WHERE owner_key = ? AND kind = ?
            AND (? OR state = 'active') AND (? IS NULL OR local_date > ?) ORDER BY local_date LIMIT ?""",
            (self._owner(owner), kind.value, include_deleted, after_date, after_date, limit))
        # Metadata does not imply that the content's source dependencies are still current.
        return tuple(codec.head_from_row(row) for row in rows)

    def _validated(self, key, at, *, expected_revision=None, forbidden=None, scope=None, recipient=None):
        batch = self._read_only_batch(at) if forbidden is None else None
        validated_cache = (
            batch.extras.setdefault("role_journal.validated", {})
            if (batch is not None and scope is None and recipient is None)
            else None
        )
        entry_cache = batch.extras.setdefault("role_journal.entries", {}) if batch is not None else None
        if validated_cache is not None:
            cached_root = validated_cache.get(key)
            if cached_root is not None:
                if expected_revision is not None and cached_root.reference.revision != expected_revision:
                    raise TaskError("journal_dependency_changed", "journal input revision is no longer current")
                return cached_root
        pending = [(key, expected_revision)]
        checked = {}
        checked_sources = set()
        visited_entries = {}
        all_unguarded = True
        result = None
        while pending:
            current, expected = pending.pop()
            if current == forbidden:
                raise TaskError("journal_dependency_cycle", "journal writing cannot depend on itself indirectly")
            if current in checked:
                if expected is not None and checked[current] != expected:
                    raise TaskError("journal_dependency_changed", "journal input revision is no longer current")
                continue
            if validated_cache is not None and current in validated_cache:
                cached_entry = validated_cache[current]
                if expected is not None and cached_entry.reference.revision != expected:
                    raise TaskError("journal_dependency_changed", "journal input revision is no longer current")
                if result is None:
                    result = cached_entry
                checked[current] = cached_entry.reference.revision
                continue
            if scope is not None and len(checked) >= 64:
                raise TaskError("journal_dependency_limit", "foreground journal ancestry exceeds its read budget")
            row = self._row(current, batch=batch)
            if row is None or row["state"] != JournalState.ACTIVE.value:
                raise TaskError("journal_unavailable", "journal date is absent or deleted")
            if expected is not None and row["revision"] != expected:
                raise TaskError("journal_dependency_changed", "journal input revision is no longer current")
            entry_key = row["entry_key"]
            entry = None
            if entry_cache is not None:
                cached_parsed = entry_cache.get(entry_key)
                if cached_parsed is not None and cached_parsed[0] == row["revision"]:
                    entry = cached_parsed[1]
            if entry is None:
                try:
                    entry = codec.entry_from_row(row)
                except (AttributeError, KeyError, TypeError, ValueError) as error:
                    raise TaskError("journal_unavailable", "stored journal content could not be decoded") from error
                if entry_cache is not None:
                    entry_cache[entry_key] = (row["revision"], entry)
            if scope is not None:
                if entry.draft.context.scope != scope or any(a.scope != scope for a in entry.activity_evidence):
                    raise TaskError("journal_context_mismatch", "journal ancestry belongs to another conversation")
                if (entry.updated_at > at
                        or current.local_date > at.astimezone(ZoneInfo(entry.draft.context.timezone)).date().isoformat()):
                    raise TaskError("journal_not_active", "journal is not available at this time")
            if result is None:
                result = entry
            checked[current] = entry.reference.revision
            visited_entries[current] = entry
            sources = list(entry.draft.sources)
            source_hashes = {}
            if scope is not None:
                for activity in entry.activity_evidence:
                    if activity.work is not None and activity.work.source is not None:
                        sources.append(activity.work.source)
                        source_hashes[activity.work.source] = activity.work.content_hash
                    if activity.reading is not None:
                        sources.append(activity.reading.source)
                        source_hashes[activity.reading.source] = activity.reading.content_hash
            for source in sources:
                check_key = (source, source_hashes.get(source))
                if check_key not in checked_sources:
                    if scope is None:
                        require(self.sources.validate_reference(source, observed_at=at))
                    else:
                        if source.scope != scope:
                            raise TaskError("journal_context_mismatch", "journal source belongs to another conversation")
                        require(self.sources.validate_reference_for_audience(source, recipient=recipient, observed_at=at,
                            expected_content_hash=source_hashes.get(source)))
                    checked_sources.add(check_key)
                    if validated_cache is not None and not is_unguarded_checked(self.runtime, source, at):
                        all_unguarded = False
            pending.extend((ref.key, ref.revision) for ref in entry.draft.journals)
        if (
            validated_cache is not None
            and all_unguarded
            and batch is not None
            and batch.active
            and batch.extras.get("role_journal.validated") is validated_cache
        ):
            for validated_key, validated_entry in visited_entries.items():
                validated_cache[validated_key] = validated_entry
        return result

    def recent_days(self, owner, *, through_date, limit=8):
        """Latest metadata for bounded writing context; content still needs get()."""
        local_day(through_date)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("recent journal page must be bounded")
        rows = self.runtime.read_all("""SELECT * FROM role_journals WHERE owner_key = ?
            AND state = 'active' AND local_date <= ? ORDER BY local_date DESC, kind LIMIT ?""",
            (self._owner(owner), through_date, limit))
        return tuple(codec.head_from_row(row) for row in rows)

    def get(self, key, *, observed_at=None):
        at = self._time(observed_at)
        with self.runtime.read_snapshot(), validation_batch(self.runtime, at):
            return self._validated(key, at)

    def get_for_audience(self, key, *, scope, recipient, observed_at=None):
        """Check the entire ancestry's scope and source audience; grants no action permission."""
        at = self._time(observed_at)
        with self.runtime.read_snapshot(), validation_batch(self.runtime, at):
            return self._validated(key, at, scope=scope, recipient=recipient)

    def context_days(self, owner, scope, *, observed_at, limit=32):
        """Recent same-scope metadata; every selected body still requires an authorized read."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("journal context page must be bounded")
        if scope.installation_id != owner.installation_id or scope.bot_id != owner.bot_id:
            raise ValueError("journal scope crosses its role owner")
        rows = self.runtime.read_all("""SELECT * FROM role_journals WHERE owner_key = ? AND state = 'active'
            AND json_extract(context_json, '$.scope') = json(?) AND updated_at <= ?
            ORDER BY local_date DESC, kind LIMIT ?""",
            (self._owner(owner), encode(scope), stamp(observed_at), limit))
        return tuple(codec.head_from_row(row) for row in rows)

    def _reserve(self, context, operation_id, parameters, at):
        operation = OperationRef(operation_id, "role.journal", context.scope, VersionRef(operation_id, 1), operation_id)
        receipt = require(self.operations.reserve(operation, parameter_hash=digest(parameters), observed_at=at))
        if not receipt.replayed:
            return None
        saved = self.runtime.read_one("SELECT result_json FROM role_journal_operations WHERE operation_id = ? AND owner_key = ?",
                                      (operation_id, self._owner(context.key.owner)))
        if saved is None or receipt.state is not OperationState.COMPLETED:
            raise TaskError("journal_operation_incomplete", "original journal operation has no committed result")
        return codec.receipt_from_json(saved["result_json"])

    def _finish(self, connection, receipt, at):
        connection.execute("INSERT INTO role_journal_operations VALUES (?, ?, ?)",
            (receipt.operation_id, self._owner(receipt.head.key.owner), encode(receipt)))
        require(self.operations.transition(receipt.operation_id, expected_state=OperationState.ACCEPTED,
            next_state=OperationState.COMPLETED, observed_at=at))
        return receipt

    def operation_receipt(self, operation_id, *, owner):
        """Read a durable write receipt without exposing journal content."""
        if type(operation_id) is not str or not operation_id or len(operation_id) > 512:
            raise ValueError("journal operation id must be explicit")
        row = self.runtime.read_one(
            "SELECT result_json FROM role_journal_operations WHERE operation_id = ? AND owner_key = ?",
            (operation_id, self._owner(owner)))
        return codec.receipt_from_json(row["result_json"]) if row is not None else None

    def has_operation_receipt(self, operation_id, *, owner):
        """Read whether a durable write receipt exists."""
        return self.operation_receipt(operation_id, owner=owner) is not None

    def save(self, draft, *, expected_revision, operation_id, observed_at=None):
        return self._write(draft.context, draft, "save", expected_revision, operation_id, observed_at)

    def restore(self, draft, *, expected_revision, operation_id, observed_at=None):
        """Explicit owner action with replacement content; automatic save cannot restore a deleted day."""
        return self._write(draft.context, draft, "restore", expected_revision, operation_id, observed_at)

    def delete(self, context, *, expected_revision, operation_id, observed_at=None):
        """Also records a tombstone for a day whose background writer has not committed yet."""
        return self._write(context, None, "delete", expected_revision, operation_id, observed_at)

    def _write(self, context, draft, action, expected_revision, operation_id, observed_at):
        revision(expected_revision, allow_zero=True)
        key, at = context.key, self._time(observed_at)
        owner_key, entry_key = self._owner(key.owner), digest(key)
        with self.runtime.transaction() as connection, validation_batch(self.runtime, at):
            replay = self._reserve(context, operation_id, [context, codec.draft_parameters(draft), action, expected_revision], at)
            if replay is not None:
                return replay
            row = self._row(key)
            head = codec.head_from_row(row) if row is not None else JournalHead(key, 0, JournalState.ABSENT, None)
            if head.revision != expected_revision:
                raise TaskError("journal_revision_conflict", "journal changed after this operation was prepared")
            if row is not None and codec.context(json.loads(row["context_json"])) != context:
                raise TaskError("journal_context_conflict", "journal date retains its original scope and timezone")
            if head.updated_at is not None and at < head.updated_at:
                raise ValueError("journal update cannot move backwards in time")
            if action == "restore" and head.state is not JournalState.DELETED:
                raise TaskError("journal_not_deleted", "only a deleted day can be restored")
            if action == "save" and head.state is JournalState.DELETED:
                raise TaskError("journal_deleted", "automatic writing cannot reopen a deleted date")
            state = JournalState.DELETED if draft is None else JournalState.ACTIVE
            updated = JournalHead(key, head.revision + 1, state, at)
            content = None
            if draft is not None:
                for source in draft.sources:
                    require(self.sources.validate_reference(source, observed_at=at))
                for ref in draft.journals:
                    self._validated(ref.key, at, expected_revision=ref.revision, forbidden=key)
                evidence = []
                for ref in draft.activities:
                    activity = self.life.activity(ref.lineage_id, owner=key.owner)
                    if activity.revision != ref.revision:
                        raise TaskError("journal_activity_changed", "activity changed after writing was prepared")
                    if activity.reading is not None and activity.reading.source not in draft.sources:
                        raise TaskError("journal_reading_source_required", "reading activity must retain its original source dependency")
                    evidence.append(activity)
                content = encode(JournalEntry(JournalRef(key, updated.revision), draft, tuple(evidence), at))
            if row is None:
                connection.execute("INSERT INTO role_journals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry_key, owner_key, key.kind.value, key.local_date, updated.revision,
                     state.value, encode(context), stamp(at), content))
            else:
                changed = connection.execute("""UPDATE role_journals SET revision = ?, state = ?, updated_at = ?, content_json = ?
                    WHERE entry_key = ? AND revision = ?""", (updated.revision, state.value, stamp(at), content, entry_key, expected_revision))
                if changed.rowcount != 1:
                    raise TaskError("journal_revision_conflict", "journal changed before commit")
            connection.execute("DELETE FROM role_journal_derivatives WHERE entry_key = ?", (entry_key,))
            return self._finish(connection, JournalReceipt(operation_id, action, updated), at)

    def save_derivatives(self, material, *, expected_revision=0, operation_id, observed_at=None):
        revision(expected_revision, allow_zero=True)
        at, key = self._time(observed_at), material.parent.key
        with self.runtime.transaction() as connection, validation_batch(self.runtime, at):
            row = self._row(key)
            if row is None:
                raise TaskError("journal_unavailable", "derived material needs a committed journal")
            context = codec.context(json.loads(row["context_json"]))
            replay = self._reserve(context, operation_id, [codec.derivative_parameters(material), expected_revision, "derive"], at)
            if replay is not None:
                return replay
            entry = self._validated(key, at, expected_revision=material.parent.revision)
            known = {item.material_id for item in entry.draft.materials}
            if any(identifier not in known for origins in material.fragment_origins for identifier in origins):
                raise TaskError("journal_material_unknown", "fragment refers to material outside its original journal")
            current = connection.execute("SELECT * FROM role_journal_derivatives WHERE entry_key = ?", (digest(key),)).fetchone()
            current_revision = current["revision"] if current is not None else 0
            if current_revision != expected_revision:
                raise TaskError("journal_derivative_conflict", "derived material has already changed")
            if at < entry.updated_at or current is not None and at < codec.derivatives_from_json(current["record_json"]).updated_at:
                raise ValueError("derived material cannot precede its parent or previous revision")
            saved = DerivativeRecord(material, current_revision + 1, at)
            connection.execute("""INSERT INTO role_journal_derivatives VALUES (?, ?, ?, ?)
                ON CONFLICT(entry_key) DO UPDATE SET entry_revision = excluded.entry_revision,
                    revision = excluded.revision, record_json = excluded.record_json""",
                (digest(key), material.parent.revision, saved.revision, encode(saved)))
            return self._finish(connection, JournalReceipt(operation_id, "derive", codec.head_from_row(row), saved.revision), at)

    def derivatives(self, key, *, observed_at=None, _validated_entry=None):
        at = self._time(observed_at)
        with self.runtime.read_snapshot(), validation_batch(self.runtime, at):
            entry = _validated_entry or self._validated(key, at)
            entry_key = digest(key)
            batch = self._read_only_batch(at)
            row = None
            hit = False
            if batch is not None:
                deriv_cache = batch.extras.get("role_journal.derivatives_rows")
                if deriv_cache is not None and entry_key in deriv_cache:
                    row = deriv_cache[entry_key]
                    hit = True
            if not hit:
                row = self.runtime.read_one(
                    "SELECT * FROM role_journal_derivatives WHERE entry_key = ?", (entry_key,)
                )
            if row is None:
                return None
            if row["entry_revision"] != entry.reference.revision:
                raise TaskError("journal_dependency_changed", "derived material belongs to an older journal")
            return codec.derivatives_from_json(row["record_json"])
