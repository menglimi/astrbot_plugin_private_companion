"""Bounded, source-governed samples for expression learning.

New learning runs use authorized inbound text from the exact private
relationship or group. The older confirmed-reply sample path remains readable
so existing rules can still be reviewed and their evidence rechecked.

The samples a learned rule cites are durable evidence: a later ``/reset`` ends
the temporary conversation context but never revokes them, so rechecking a
stored rule reads its governed source and only labels the reset (see
``evidence_reset_at``). Fresh learning samples stay inside the current window.

Samples are form only. They are never republished as facts about the role or
about anyone else, and the prompt contract forbids inferring identity,
personality, mood, health, relationships or stable preference from them.

Every bound is a default for the registered ``expression.learning`` settings;
callers pass the effective values so an operator can widen the window or the
batch without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, stamp
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, SourceRef, SubjectKind, VersionRef
from companion_v2.identity_policy.group_audience import group_audience

from .sample_review import ExpressionSampleReviewStore


READ_ACTION = "role.expression.read"
REPLY_OWNER = "conversation.confirmed_reply"
REPLY_SAMPLE_SOURCE = "confirmed_reply"
INPUT_SAMPLE_SOURCE = "conversation_input"
MAX_SAMPLE_LIMIT = 64
MAX_SAMPLE_CHARS = 4000
MAX_WINDOW_HOURS = 720
MIN_SAMPLES = 1
DEFAULT_SAMPLE_LIMIT = 60
DEFAULT_SAMPLE_CHARS = 800


@dataclass(frozen=True)
class StyleSample:
    handle: str
    text: str
    sender_id: str | None = None
    received_at: datetime | None = None
    input_sequence: int = 0
    content_hash: str | None = None


@dataclass(frozen=True)
class SampleSet:
    opened_at: datetime
    closed_at: datetime
    samples: tuple[StyleSample, ...]
    source_kind: str = REPLY_SAMPLE_SOURCE

    @property
    def material_hash(self):
        return digest([self.source_kind, *({"handle": item.handle, "text": item.text} for item in self.samples)])


def sample_handles(samples):
    return {item.handle for item in samples.samples}


def sample_document(samples):
    return {"opened_at": stamp(samples.opened_at), "closed_at": stamp(samples.closed_at),
            "handles": [item.handle for item in samples.samples],
            "characters": sum(len(item.text) for item in samples.samples),
            "source_kind": samples.source_kind}


def _trim(value, maximum=DEFAULT_SAMPLE_CHARS):
    value = value.strip()
    return value if len(value) <= maximum else value[:maximum]


class ExpressionSampleCatalog:
    """Recheckable expression samples from one exact private or group scope."""

    def __init__(self, runtime, *, window_hours=720):
        if type(window_hours) is not int or not 1 <= window_hours <= MAX_WINDOW_HOURS:
            raise ValueError("expression sample window must stay inside its bounded range")
        self.runtime = runtime
        self.messages = None
        self.window = timedelta(hours=window_hours)
        self.reviews = ExpressionSampleReviewStore(runtime)

    def attach(self, messages):
        if messages.runtime is not self.runtime:
            raise ValueError("expression samples must share their conversation runtime")
        self.messages = messages

    def _owner(self):
        if self.messages is None:
            raise TaskError("expression_sample_owner_unavailable",
                            "the conversation owner is not attached")
        return self.messages

    @staticmethod
    def role_owner(context):
        """The role that owns these samples, in the same shape as other role ledgers."""
        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    @staticmethod
    def _authorize(context, at):
        if context.scope.kind is ScopeKind.PRIVATE:
            if (context.audience.recipients != (context.actor.subject,)
                    or context.actor.subject.kind is not SubjectKind.HUMAN):
                raise TaskError("expression_private_audience_required",
                                "style learning needs the current participant's exact private audience")
            return ScopeKind.PRIVATE
        if context.scope.kind is ScopeKind.GROUP:
            if context.audience != group_audience(context.scope, context.actor.subject):
                raise TaskError("expression_group_audience_required",
                                "group style learning needs the collective audience of that group")
            return ScopeKind.GROUP
        raise TaskError("expression_scope_required",
                        "style learning reads one private or group conversation")

    @staticmethod
    def sample_handle(context, part_id):
        return "sample:" + digest([context.key, part_id])

    @staticmethod
    def _original(row):
        return TaskContext.restore(json.loads(row["context_json"]))

    def _text(self, row, original, at):
        owner = getattr(self._owner(), "confirmed_reply_sources", None)
        if owner is None:
            raise TaskError("expression_sample_owner_unavailable",
                            "the confirmed reply owner is not attached")
        reference = SourceRef("sent:" + row["part_id"], REPLY_OWNER, original.scope,
                              VersionRef(row["part_id"], 1), at)
        return owner.materialize(original, reference).payload

    @staticmethod
    def input_handle(event):
        return "input:" + digest([event.scope, event.scoped_key])

    def _input_rows(self, context, opened_at, at, *, limit):
        clause = ""
        values = [stamp(opened_at), stamp(at), context.scope.kind.value]
        if context.scope.kind is ScopeKind.PRIVATE:
            clause = "AND b.origin_key = ? "
            values.append(context.key)
        else:
            clause = "AND json_extract(b.context_json, '$.role_binding') = ? "
            values.append(encode(context.role_binding))
        values.append(limit)
        legacy = self.runtime.read_all("""SELECT COALESCE(s.rowid, i.rowid) AS input_sequence,
            i.event_key, i.event_json, b.context_json
            FROM conversation_inputs i JOIN conversation_batches b ON b.batch_id = i.batch_id
            LEFT JOIN sources s ON s.source_id = json_extract(i.event_json, '$.source.source_id')
                AND s.lineage_id = json_extract(i.event_json, '$.source.version.lineage_id')
                AND s.source_revision = json_extract(i.event_json, '$.source.version.revision')
            WHERE i.withdrawn = 0 AND json_extract(i.event_json, '$.received_at') >= ?
              AND json_extract(i.event_json, '$.received_at') <= ?
              AND json_extract(i.event_json, '$.scope.kind') = ?
              """ + clause + "ORDER BY json_extract(i.event_json, '$.received_at') DESC, i.rowid DESC LIMIT ?",
            tuple(values))
        from companion_v2.conversation.native_observations import inbound_event, input_rows_for_context
        native = input_rows_for_context(self.runtime, context, start=stamp(opened_at), end=stamp(at), limit=limit)
        if not native:
            return legacy
        rows = [dict(row) for row in legacy]
        for row in native:
            event = inbound_event(row)
            rows.append({"input_sequence": row["input_sequence"], "event_key": event.scoped_key,
                         "event_json": encode(event), "context_json": row["context_json"],
                         "native_observation": row})
        rows.sort(key=lambda row: (json.loads(row["event_json"])["received_at"], row["input_sequence"]), reverse=True)
        return tuple(rows[:limit])

    def _input_text(self, row, context, at, *, durable=False):
        from companion_v2.conversation.messages import event_from_document

        try:
            original = TaskContext.restore(json.loads(row["context_json"]))
            event = event_from_document(json.loads(row["event_json"]), original)
        except (TypeError, ValueError, KeyError):
            raise TaskError("expression_sample_changed", "original conversation sample is invalid") from None
        if (original.scope != context.scope or original.role_binding != context.role_binding
                or event.scope != context.scope or event.principal != original.actor.principal
                or (context.scope.kind is ScopeKind.PRIVATE and
                    (original.actor.principal != context.actor.principal
                     or original.actor.subject != context.actor.subject))):
            raise TaskError("expression_sample_changed", "original conversation sample belongs to another scope")
        self._authorize(original, at)
        messages = self._owner()
        messages.authorize(original, at)
        if "native_observation" in row.keys():
            from companion_v2.conversation.native_observations import validate_observation
            from companion_v2.conversation.reset import source_current
            validate_observation(self.runtime, row["native_observation"], original, event.source)
            # A reset only ends the temporary context: fresh learning samples
            # must belong to the current window, while a stored rule's evidence
            # rechecks its governed source below (state, expiry, hash, recall).
            if not durable and not source_current(self.runtime, event.source):
                raise TaskError("conversation_context_reset", "the native sample precedes this window's reset")
        record = messages._read_source(event.source, original, event.capability, at)
        if record.content_hash != event.input_hash:
            raise TaskError("expression_sample_changed", "original conversation sample changed")
        return event, record.payload

    def _collect_inputs(self, context, opened_at, at, *, sample_limit, sample_chars, member_ids=None,
                        review_enabled=None):
        rows = self._input_rows(context, opened_at, at, limit=512)
        samples = []
        for row in rows:
            try:
                event, text = self._input_text(row, context, at)
            except TaskError:
                continue
            if member_ids is not None and event.principal.native_account_id not in member_ids:
                continue
            content_hash = digest(text)
            text = _trim(text, sample_chars)
            if text:
                sample = StyleSample(self.input_handle(event), text, event.principal.native_account_id,
                                     event.received_at, row["input_sequence"], content_hash)
                if review_enabled is not None and not self.reviews.eligible(
                        context, sample, review_enabled=review_enabled):
                    continue
                samples.append(sample)
                if len(samples) == sample_limit:
                    break
        from .legacy_import import samples as legacy_samples
        for sample in legacy_samples(self.runtime, context, opened_at=opened_at, closed_at=at):
            if member_ids is not None and sample.sender_id not in member_ids:
                continue
            if review_enabled is not None and not self.reviews.eligible(context, sample, review_enabled=review_enabled):
                continue
            samples.append(StyleSample(sample.handle, _trim(sample.text, sample_chars), sample.sender_id,
                                       sample.received_at, sample.input_sequence, sample.content_hash))
        samples.sort(key=lambda item: (stamp(item.received_at), item.handle), reverse=True)
        return tuple(samples[:sample_limit])

    def _rows(self, context, opened_at, at, *, scope_kind, sample_limit):
        # This query is retained for already-created role-reply evidence.
        actor_clause, actor_values = "", ()
        if scope_kind is ScopeKind.PRIVATE:
            actor_clause = "AND json_extract(b.context_json, '$.actor.subject.subject_id') = ? "
            actor_values = (context.actor.subject.subject_id,)
        return self.runtime.read_all(f"""SELECT h.part_id, h.history_sequence, b.context_json
            FROM conversation_sent_history h
            JOIN conversation_parts p ON p.part_id = h.part_id
            JOIN conversation_batches b ON b.batch_id = h.batch_id
            JOIN conversation_delivery_receipts r ON r.part_id = h.part_id AND r.receipt_ref = h.receipt_ref
            WHERE p.state = 'delivered' AND p.delivered_at > ? AND p.delivered_at <= ?
            AND json_extract(b.context_json, '$.audience.scope.kind') = ?
            AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
            AND json_extract(b.context_json, '$.role_binding.scope.bot_id') = ?
            AND json_extract(b.context_json, '$.audience.scope.account_id') = ?
            {actor_clause}
            ORDER BY h.history_sequence DESC LIMIT ?""",
            (stamp(opened_at), stamp(at), scope_kind.value, context.role_binding.role.subject_id,
             context.scope.bot_id, context.scope.account_id, *actor_values, sample_limit))

    def collect(self, context, at, *, window_hours=None, sample_limit=DEFAULT_SAMPLE_LIMIT,
                sample_chars=DEFAULT_SAMPLE_CHARS, source_kind=REPLY_SAMPLE_SOURCE, member_ids=None,
                review_enabled=None):
        """Read one bounded set and recheck each item's original governed source."""
        scope_kind = self._authorize(context, at)
        window = self.window if window_hours is None else timedelta(hours=window_hours)
        if (type(sample_limit) is not int or not 1 <= sample_limit <= MAX_SAMPLE_LIMIT
                or type(sample_chars) is not int or not 1 <= sample_chars <= MAX_SAMPLE_CHARS):
            raise TaskError("expression_sample_bounds_invalid", "sample bounds are outside their range")
        if source_kind not in (REPLY_SAMPLE_SOURCE, INPUT_SAMPLE_SOURCE):
            raise TaskError("expression_sample_source_invalid", "unknown expression sample source")
        opened_at = at - window
        with self.runtime.read_snapshot():
            self._owner().authorize(context, at)
            if source_kind == INPUT_SAMPLE_SOURCE:
                samples = self._collect_inputs(context, opened_at, at, sample_limit=sample_limit,
                    sample_chars=sample_chars, member_ids=member_ids, review_enabled=review_enabled)
                return SampleSet(opened_at, at, samples, source_kind)
            samples = []
            for row in self._rows(context, opened_at, at, scope_kind=scope_kind, sample_limit=sample_limit):
                try:
                    text = _trim(self._text(row, self._original(row), at), sample_chars)
                except TaskError:
                    continue
                if text:
                    samples.append(StyleSample(self.sample_handle(context, row["part_id"]), text))
            return SampleSet(opened_at, at, tuple(samples), source_kind)

    def new_inputs(self, context, at, *, after_sequence=0, window_hours=720, member_ids=None):
        """Count source-readable new messages, before the smaller learning sample cap."""
        rows = self._input_rows(context, at - timedelta(hours=window_hours), at, limit=512)
        count, latest = 0, after_sequence
        for row in rows:
            if row["input_sequence"] <= after_sequence:
                continue
            try:
                event, text = self._input_text(row, context, at)
            except TaskError:
                continue
            if text.strip() and (member_ids is None or event.principal.native_account_id in member_ids):
                count += 1
                latest = max(latest, row["input_sequence"])
        return count, latest

    def inspect_inputs(self, context, at, *, window_hours, sample_chars, member_ids=None):
        """Expose the bounded source pool, independently of the smaller learning batch."""
        self._authorize(context, at)
        opened_at = at - timedelta(hours=window_hours)
        with self.runtime.read_snapshot():
            self._owner().authorize(context, at)
            samples = self._collect_inputs(context, opened_at, at, sample_limit=512,
                sample_chars=sample_chars, member_ids=member_ids)
        return SampleSet(opened_at, at, samples, INPUT_SAMPLE_SOURCE)

    def resolve_handles(self, handles, opened_at, closed_at, context, at, *, source_kind=REPLY_SAMPLE_SOURCE):
        """Recheck a named set of sample handles inside one closed window."""
        self.describe_handles(handles, opened_at, closed_at, context, at, source_kind=source_kind,
                              durable=True)
        return True

    def evidence_reset_at(self, context, closed_at):
        """The window reset this stored evidence predates, for a review label.

        Evidence a learned rule cites is durable, so a reset neither hides nor
        invalidates it; when the whole sample window closed before the window's
        current boundary the review page can still say the evidence is
        historical. ``None`` means no reset applies to this evidence.
        """
        from companion_v2.conversation.reset import durable_reset_at
        moment = durable_reset_at(self.runtime, context.scope)
        return moment if moment is not None and stamp(closed_at) < moment else None

    def describe_handles(self, handles, opened_at, closed_at, context, at, *,
                         source_kind=REPLY_SAMPLE_SOURCE, durable=False):
        """Return named samples only after rechecking their original source.

        ``durable`` says the handles are evidence already stored for a rule or
        fact: a window reset dates it but never revokes it. A fresh learning
        batch keeps the default and stays inside the current window.
        """
        scope_kind = self._authorize(context, at)
        requested = tuple(handles)
        known = set(requested)
        if not known:
            raise TaskError("expression_sample_unavailable", "this run has no samples to recheck")
        if len(known) != len(requested):
            raise TaskError("expression_sample_unavailable", "this run has duplicate sample handles")
        if source_kind not in (REPLY_SAMPLE_SOURCE, INPUT_SAMPLE_SOURCE):
            raise TaskError("expression_sample_changed", "stored expression sample source is invalid")
        if source_kind == INPUT_SAMPLE_SOURCE:
            with self.runtime.read_snapshot():
                self._owner().authorize(context, at)
                descriptions = {}
                from .legacy_import import samples as legacy_samples
                for sample in legacy_samples(self.runtime, context, opened_at=opened_at, closed_at=closed_at):
                    if sample.handle in known:
                        descriptions[sample.handle] = sample.text
                        known.discard(sample.handle)
                for row in self._input_rows(context, opened_at, closed_at, limit=512):
                    try:
                        event, text = self._input_text(row, context, at, durable=durable)
                    except TaskError:
                        continue
                    handle = self.input_handle(event)
                    if handle in known:
                        descriptions[handle] = text
                        known.discard(handle)
                if known:
                    raise TaskError("expression_sample_changed", "original conversation samples are no longer available")
                return [descriptions[handle] for handle in requested]
        # The run stored its own batch size, so rechecking never depends on the
        # current configuration and a narrowed batch cannot invalidate a run.
        limit = min(MAX_SAMPLE_LIMIT, max(len(known) * 2, len(known) + 8))
        descriptions = {}
        with self.runtime.read_snapshot():
            self._owner().authorize(context, at)
            for row in self._rows(context, opened_at, closed_at, scope_kind=scope_kind, sample_limit=limit):
                handle = self.sample_handle(context, row["part_id"])
                if handle not in known:
                    continue
                descriptions[handle] = self._text(row, self._original(row), at)
                known.discard(handle)
        if known:
            raise TaskError("expression_sample_changed",
                            "original expression samples are no longer available")
        return [descriptions[handle] for handle in requested]

    def catalogue(self, samples):
        return [{"handle": item.handle, "text": item.text} for item in samples.samples]

    def evidence_context(self, window, context):
        key = window.get("context_key")
        if key is None:
            if context.scope.kind is ScopeKind.PRIVATE:
                return context
            raise TaskError("expression_sample_changed", "original group sample context is unavailable")
        if context.scope.kind is ScopeKind.GROUP:
            if type(key) is not str or len(key) != 64:
                raise TaskError("expression_sample_changed", "original group sample context is invalid")
            rows = self.runtime.read_all("""SELECT context_json FROM conversation_batches
                WHERE origin_key = ? ORDER BY rowid DESC LIMIT 1""", (key,))
            if not rows:
                try:
                    rows = self.runtime.read_all("""SELECT context_json FROM conversation_native_observations
                        WHERE context_key = ? AND kind = 'input' AND delivery_state = 'input_observed'
                        ORDER BY observed_at DESC, rowid DESC LIMIT 1""",
                        (key,))
                except Exception:
                    rows = ()
            for row in rows:
                try:
                    original = TaskContext.restore(json.loads(row["context_json"]))
                except (TypeError, ValueError, KeyError):
                    continue
                if (original.key == key and original.scope == context.scope
                        and original.role_binding == context.role_binding):
                    return original
            raise TaskError("expression_sample_changed", "original sample belongs to another group or role")
        from .private_scope import current_private_context
        original = current_private_context(self.runtime, key, context.role_binding)
        same_role = (original is not None
            and original.scope.installation_id == context.scope.installation_id
            and original.scope.bot_id == context.scope.bot_id
            and original.role_binding.role == context.role_binding.role)
        if not same_role or original.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("expression_sample_changed", "original sample belongs to another role or window")
        return original

    def private_source_available(self, source, destination, at):
        """Recheck source authority and role selection before cross-relationship use."""
        if (source.scope.kind is not ScopeKind.PRIVATE
                or destination.scope.kind is not ScopeKind.PRIVATE
                or source.scope.installation_id != destination.scope.installation_id
                or source.scope.bot_id != destination.scope.bot_id
                or source.role_binding.role != destination.role_binding.role
                or source.actor.subject.kind is not SubjectKind.HUMAN):
            return False
        identity = self._owner().tasks.identity
        resolved = identity.resolve_authenticated(source.actor.principal, observed_at=at)
        role = identity.resolve_role(source.scope, observed_at=at)
        if (not resolved.is_success or not role.is_success
                or resolved.unwrap().subject != source.actor.subject
                or resolved.unwrap().binding_id != source.actor.binding_id
                or resolved.unwrap().binding_revision != source.actor.binding_revision
                or role.unwrap() != source.role_binding):
            return False
        try:
            self._owner().authorize(source, at)
            from .configuration import (application_subject_allowed_for_context, read_settings,
                                        source_subject_allowed_for_context)
            source_policy = read_settings(self.runtime, source)
            return (source_policy.private_learning_enabled
                    and source_subject_allowed_for_context(self.runtime, source)
                    and application_subject_allowed_for_context(self.runtime, destination))
        except Exception:
            return False
