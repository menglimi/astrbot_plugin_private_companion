"""Authorized projections of recorded role life, without executing background work."""

from dataclasses import dataclass
import json
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.role_work.journal.models import JournalKey, JournalKind
from companion_v2.role_work.models import RoleActivityState as ActivityState
from companion_v2.sources.privacy import redact_text


READ_ACTION = "role.life.read"
SCAN_LIMIT = 32


@dataclass(frozen=True)
class LifeMaterial:
    reference: dict
    purpose: str
    payload: str


@dataclass(frozen=True)
class LifeCandidates:
    variants: tuple[tuple[LifeMaterial, ...], ...]
    truncated: bool
    skipped: int


def material(reference, purpose, payload):
    return LifeMaterial(reference, purpose, json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False))


class RoleLifeContextReader:
    """The read boundary for one private participant, current role and exact scope.

    References retain the selected representation. A summary created later cannot
    silently replace a body, and neither representation becomes factual evidence.
    """

    def __init__(self, life, journal, tasks):
        if life.runtime is not journal.runtime or life.runtime is not tasks.runtime:
            raise ValueError("life context must share its original owners and runtime")
        self.life, self.journal, self.tasks = life, journal, tasks
        self.runtime = tasks.runtime

    @staticmethod
    def owner(context):
        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    def authorize(self, context, at):
        require(self.tasks.authorize_context(context, action=READ_ACTION, observed_at=at))

    def _source(self, source, context, at, content_hash=None):
        if source.scope != context.scope:
            raise TaskError("life_context_scope", "life source belongs to another conversation")
        require(self.journal.sources.validate_reference_for_audience(source,
            recipient=context.actor.subject, observed_at=at, expected_content_hash=content_hash))

    @staticmethod
    def _reading_source_id(activity):
        if activity.work is not None and activity.work.source is not None:
            return activity.work.source.source_id
        if activity.reading is not None:
            return activity.reading.source.source_id
        return None

    @staticmethod
    def _is_reading_activity(activity):
        return (activity.work is not None and activity.work.kind == "reading") or activity.reading is not None

    def _activity(self, activity, context, at, *, book_progress=None):
        if (activity.owner != self.owner(context) or activity.scope != context.scope
                or activity.work is not None and activity.work.context_key != context.key):
            raise TaskError("life_context_scope", "activity belongs to another original authority")
        if activity.updated_at > at:
            raise TaskError("life_context_not_active", "activity is not available at this time")
        work, reading = activity.work, activity.reading
        if work is not None and work.source is not None:
            self._source(work.source, context, at, work.content_hash)
        if reading is not None:
            self._source(reading.source, context, at, reading.content_hash)
        payload = {"basis": "recorded_role_activity", "kind": work.kind if work else
                   ("reading" if reading else "activity"), "state": activity.state.value,
                   "description": activity.description, "checkpoint": activity.checkpoint,
                   "started_at": activity.started_at.isoformat(), "updated_at": activity.updated_at.isoformat()}
        if work is not None and work.due_at is not None:
            payload["planned_wake_at"] = work.due_at.isoformat()
        if work is not None and work.kind == "reading" and reading is None:
            payload["planned_reading_span"] = {"start_offset": work.start_offset,
                "end_offset": work.end_offset, "source_key": digest(work.source)}
        source_id = self._reading_source_id(activity)
        progress = book_progress.get(source_id) if isinstance(book_progress, dict) and source_id else None
        if progress is None and work is not None and work.kind == "reading" and work.source is not None:
            progress = self.life.book_progress_for_sources(
                activity.owner, activity.scope, (work.source.source_id,), observed_at=at
            ).get(work.source.source_id)
        if progress is not None:
            payload["book_progress"] = dict(progress)
        if reading is not None:
            reading_payload = {"basis": "completed_reading_receipt",
                "source_key": digest(reading.source), "start_offset": reading.start_offset,
                "end_offset": reading.end_offset, "text_length": reading.text_length,
                "covers_source": reading.covers_source, "completed_at": reading.completed_at.isoformat(),
                "reflection": reading.reflection, "reflection_basis": "subjective_reading_response"}
            if progress is not None and progress.get("completed_book"):
                reading_payload["book_completed"] = True
            payload["reading"] = reading_payload
        return material({"kind": "activity", "activity_id": activity.activity_id,
            "revision": activity.revision}, "role_life_activity", payload)

    def _entry(self, key, context, at):
        entry = self.journal.get_for_audience(key, scope=context.scope,
            recipient=context.actor.subject, observed_at=at)
        if (key.owner != self.owner(context)
                or key.local_date > at.astimezone(ZoneInfo(entry.draft.context.timezone)).date().isoformat()):
            raise TaskError("life_context_scope", "journal is outside the current role or local date")
        return entry

    @staticmethod
    def _journal(entry, derived=None):
        draft, key = entry.draft, entry.reference.key
        payload = {"basis": "subjective_" + key.kind.value, "kind": key.kind.value,
            "local_date": key.local_date, "timezone": draft.context.timezone,
            "updated_at": entry.updated_at.isoformat(), "origin": draft.origin.value,
            "form": draft.form, "recall": draft.recall.value if draft.recall else None}
        if derived is None:
            payload.update(content_form="body", body=draft.body, afterglow=draft.afterglow)
        else:
            payload.update(content_form="derived", summary=derived.material.summary,
                dream_fragments=derived.material.dream_fragments, continuity=derived.material.continuity)
        return material({"kind": "journal", "journal_kind": key.kind.value,
            "local_date": key.local_date, "revision": entry.reference.revision,
            "derivative_revision": derived.revision if derived else 0}, "role_life_" + key.kind.value, payload)

    def candidates(self, context, at):
        with self.runtime.read_snapshot():
            self.authorize(context, at)
            owner = self.owner(context)
            activities = self.life.context_activities(owner, context.scope, observed_at=at, limit=SCAN_LIMIT)
            reading_rows = self.life.context_reading_activities(owner, context.scope, observed_at=at, limit=48)
            journals = self.journal.context_days(owner, context.scope, observed_at=at, limit=SCAN_LIMIT)
            # Deduplicate reading chunks by book source so one multi-chunk book
            # or an old paused chunk never crowds out newly finished books, and
            # balance reading vs non-reading activities so sleep logs never
            # starve the bookshelf context.
            reading_by_book = {}
            reading_source_ids = []
            for activity in reading_rows:
                sid = self._reading_source_id(activity) or activity.activity_id
                reading_source_ids.append(sid)
                existing = reading_by_book.get(sid)
                if existing is None:
                    reading_by_book[sid] = activity
                    continue
                # Prefer active > completed (newest/highest offset) > paused > planned.
                rank = {ActivityState.ACTIVE: 0, ActivityState.COMPLETED: 1,
                        ActivityState.PAUSED: 2, ActivityState.PLANNED: 3}
                new_key = (rank.get(activity.state, 4), -activity.updated_at.timestamp(),
                           -(activity.work.end_offset if activity.work else 0))
                old_key = (rank.get(existing.state, 4), -existing.updated_at.timestamp(),
                           -(existing.work.end_offset if existing.work else 0))
                if new_key < old_key:
                    reading_by_book[sid] = activity
            book_progress = self.life.book_progress_for_sources(
                owner, context.scope, reading_source_ids, observed_at=at
            )
            ordered_readings = sorted(
                reading_by_book.values(),
                key=lambda item: (
                    0 if item.state is ActivityState.ACTIVE else 1,
                    -item.updated_at.timestamp(),
                    item.activity_id,
                ),
            )
            other_activities = [item for item in activities if not self._is_reading_activity(item)]
            # Guarantee balanced slots across sleep/other and books within MAX_ACTIVITIES (6):
            # active non-reading activities (e.g. currently sleeping) + up to 4 books + up to 2 other,
            # then any remaining records so single-kind callers keep filling all 6 slots.
            active_others = [item for item in other_activities if item.state is ActivityState.ACTIVE]
            rest_others = [item for item in other_activities if item.state is not ActivityState.ACTIVE]
            merged = []
            seen_ids = set()
            for group in (active_others, ordered_readings[:4], rest_others[:2],
                          ordered_readings[4:], rest_others[2:]):
                for item in group:
                    if item.activity_id not in seen_ids:
                        seen_ids.add(item.activity_id)
                        merged.append(item)
            variants, skipped = [], 0
            for activity in merged:
                try:
                    variants.append((self._activity(activity, context, at, book_progress=book_progress),))
                except TaskError:
                    skipped += 1
            for head in journals:
                try:
                    entry = self._entry(head.key, context, at)
                    derived = self.journal.derivatives(head.key, observed_at=at)
                    options = (self._journal(entry),)
                    if derived is not None and (derived.material.summary or derived.material.dream_fragments
                                                or derived.material.continuity):
                        options = (self._journal(entry, derived),) + options
                    variants.append(options)
                except TaskError:
                    skipped += 1
            return LifeCandidates(tuple(variants),
                len(activities) == SCAN_LIMIT or len(reading_rows) == 48 or len(journals) == SCAN_LIMIT,
                skipped)

    def _reading_share_in_group_enabled(self, context):
        from .configuration import DEFAULTS, LifeConfigurationOwner, configuration_fields, target_for, values
        try:
            store = getattr(self.runtime, "configuration_store", self.runtime)
            snapshot = LifeConfigurationOwner(store).read(target_for(context), configuration_fields())
            return bool(values(snapshot).get("reading_share_in_group", DEFAULTS["reading_share_in_group"]))
        except Exception:
            return bool(DEFAULTS["reading_share_in_group"])

    def public_reading_summaries(self, context, at, *, max_books=4):
        """Project sanitized, opt-in role-wide reading summaries without exposing private sources or participants."""
        if not self._reading_share_in_group_enabled(context):
            return ()
        with self.runtime.read_snapshot():
            owner = self.owner(context)
            rows = self.life.role_reading_activities(owner, observed_at=at, limit=48)
            if not rows:
                return ()
            by_book = {}
            for activity in rows:
                source = (activity.reading.source if activity.reading is not None
                          else activity.work.source if activity.work is not None else None)
                if source is None:
                    continue
                # The exact source version must still pass governance (state, expiry,
                # dependencies); a withdrawn or expired book is never projected.
                if not self.journal.sources.validate_reference(source, observed_at=at).is_success:
                    continue
                key = (activity.scope.key, source.source_id)
                existing = by_book.get(key)
                if existing is None or activity.updated_at > existing[0].updated_at:
                    by_book[key] = (activity, source)
            if not by_book:
                return ()
            # Compute book progress per owning scope.
            by_scope = {}
            for (scope_key, source_id), (activity, _) in by_book.items():
                by_scope.setdefault(scope_key, (activity.scope, []))[1].append(source_id)
            progress_map = {}
            for scope_key, (scope_obj, sids) in by_scope.items():
                scoped = self.life.book_progress_for_sources(owner, scope_obj, sids, observed_at=at)
                for sid, prog in scoped.items():
                    progress_map[(scope_key, sid)] = prog
            ranked = sorted(
                by_book.items(),
                key=lambda item: (
                    0 if item[1][0].state is ActivityState.ACTIVE else 1,
                    -item[1][0].updated_at.timestamp(),
                ),
            )[:max(1, min(8, int(max_books)))]
            materials = []
            for (scope_key, source_id), (activity, _) in ranked:
                progress = progress_map.get((scope_key, source_id))
                completed_book = bool(progress and progress.get("completed_book")) or bool(
                    activity.reading and activity.reading.covers_source
                )
                title = redact_text((activity.description or "书架读物").strip())[:120]
                if not title:
                    title = "书架读物"
                payload = {
                    "basis": "role_public_reading_summary",
                    "title": title,
                    "state": "reading" if activity.state is ActivityState.ACTIVE else "completed",
                    "completed_book": completed_book,
                    "updated_at": activity.updated_at.isoformat(),
                }
                if progress is not None:
                    payload["book_progress"] = dict(progress)
                # Reading reflections respond to a private source and may quote it, so
                # this cross-audience projection carries only title, state and progress.
                materials.append(material(
                    {"kind": "public_reading", "book_key": digest([owner, source_id])},
                    "role_public_reading",
                    payload,
                ))
            return tuple(materials)

    def read(self, references, context, at):
        """Reconstruct only the original selection, under current source governance."""
        with self.runtime.read_snapshot():
            self.authorize(context, at)
            result = []
            for reference in references:
                if reference["kind"] == "activity":
                    try:
                        activity = self.life.activity(reference["activity_id"], owner=self.owner(context))
                    except KeyError:
                        raise TaskError("life_context_unavailable", "original activity is unavailable") from None
                    if activity.revision != reference["revision"]:
                        raise TaskError("life_context_changed", "original activity changed")
                    result.append(self._activity(activity, context, at))
                else:
                    key = JournalKey(self.owner(context), JournalKind(reference["journal_kind"]), reference["local_date"])
                    entry = self._entry(key, context, at)
                    if entry.reference.revision != reference["revision"]:
                        raise TaskError("life_context_changed", "original journal changed")
                    derived = None
                    if reference["derivative_revision"]:
                        derived = self.journal.derivatives(key, observed_at=at)
                        if derived is None or derived.revision != reference["derivative_revision"]:
                            raise TaskError("life_context_changed", "original journal summary or fragments changed")
                    result.append(self._journal(entry, derived))
            return tuple(result)
