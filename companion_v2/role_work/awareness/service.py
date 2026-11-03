"""Role-level cross-window presence: one bounded view, audience-filtered.

This service does not decide who may speak, send, or read a source.  It records
only that the same role recently saw the same human in another window and
projects a small metadata view back into the current prompt.  The two audiences
are deliberately asymmetric:

* a private turn may see the current person's own recent public group wording;
* a group turn may see only that the current speaker is also a private contact,
  never the private content or its timing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import unicodedata

from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, VersionRef, normalize_utc

from .models import ATTENTION_MINUTES, MAX_PROMPT_ITEMS, WINDOW_HOURS, valid_document
from .store import RoleAwarenessStore


def _when(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


CONTINUITY_SIMILARITY = 0.62
HANDOFF_CUES = frozenset({"来了", "我来了", "继续", "继续吧", "然后呢", "刚才那个", "刚才说的", "那个"})
HANDOFF_REPLY_MARKERS = ("私聊", "来找我", "来私聊", "到这里", "继续")


def _normalized_topic(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _topic_similarity(left, right):
    """Cheap deterministic near-duplicate score for short cross-window text."""
    left, right = _normalized_topic(left), _normalized_topic(right)
    if len(left) < 4 or len(right) < 4:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) >= 5 and (left in right or right in left):
        return 0.92
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    left_grams = {left[index:index + 2] for index in range(len(left) - 1)}
    right_grams = {right[index:index + 2] for index in range(len(right) - 1)}
    union = left_grams | right_grams
    jaccard = len(left_grams & right_grams) / len(union) if union else 0.0
    return max(ratio, jaccard)


def _source_ref(value):
    if type(value) is not dict or type(value.get("scope")) is not dict or type(value.get("version")) is not dict:
        return None
    try:
        return SourceRef(value["source_id"], value["owner"], ScopeRef.restore(value["scope"]),
                         VersionRef(**value["version"]), datetime.fromisoformat(value["issued_at"]),
                         datetime.fromisoformat(value["expires_at"]) if value.get("expires_at") else None)
    except (KeyError, TypeError, ValueError):
        return None


class RoleAwareness:
    def __init__(self, runtime, *, acquaintance=None, store=None, source_validator=None):
        self.runtime = runtime
        self.acquaintance = acquaintance
        self.store = store or RoleAwarenessStore(runtime)
        self.source_validator = source_validator

    def enabled(self, context) -> bool:
        return self._enabled(context, self._values(context))

    def _values(self, context):
        settings = getattr(self.acquaintance, "settings", None)
        reader = getattr(settings, "read", None)
        if not callable(reader):
            return {}
        try:
            values = reader(context)
        except Exception:
            return {}
        return values if isinstance(values, dict) else {}

    def _enabled(self, context, values) -> bool:
        """The acquaintance switch and the explicit cross-window switch both gate it."""
        if values:
            return (values.get("person.acquaintance_enabled", True) is not False
                    and values.get("person.cross_window_awareness_enabled", True) is not False)
        acquaintance = self.acquaintance
        available = getattr(acquaintance, "available", None)
        if not callable(available):
            return False
        try:
            return bool(available(context))
        except Exception:
            return False

    @staticmethod
    def _integer(values, name, default, minimum, maximum):
        value = values.get(name, default)
        return value if type(value) is int and minimum <= value <= maximum else default

    def peer_minutes(self, context, values=None):
        values = self._values(context) if values is None else values
        return self._integer(values, "person.cross_window_peer_minutes", 5, 1, 60)

    def record_input(self, connection, context, at, text, event_key="", *, source=None, peer_count=0,
                     quote_sender_ids=(), enabled=None):
        if enabled is False or (enabled is None and not self.enabled(context)):
            return None
        return self.store.observe_input(connection, context, normalize_utc(at, field="awareness time"),
                                        text, event_key, source, peer_count, quote_sender_ids)

    def record_reply(self, connection, context, at, *, text="", event_key="", enabled=None):
        if enabled is False or (enabled is None and not self.enabled(context)):
            return None
        return self.store.observe_reply(connection, context, normalize_utc(at, field="awareness time"),
                                        text=text, event_key=event_key)

    def snapshot(self, context, at, *, current_text=""):
        at = normalize_utc(at, field="awareness time")
        values = self._values(context)
        if not self._enabled(context, values):
            return None
        document = self.store.read(context)
        if document is None or not valid_document(document):
            return None
        if context.scope.kind is ScopeKind.PRIVATE:
            hours = self._integer(values, "person.cross_window_private_group_hours", WINDOW_HOURS, 1, 48)
            limit = self._integer(values, "person.cross_window_max_items", MAX_PROMPT_ITEMS, 1, 8)
            minutes = self._integer(values, "person.cross_window_attention_minutes", ATTENTION_MINUTES, 0, 120)
            peer_reply_minutes = self._integer(values, "person.cross_window_peer_reply_minutes", 60, 1, 720)
            recent = self._recent_group_activity(document, context, at, hours=hours, limit=limit)
            attention = self._attention(document, context, at, minutes_limit=minutes)
            peer_replies, peer_repliers = self._peer_replies(
                document, context, at, minutes=peer_reply_minutes)
            continuity = self._continuity(document, context, at, current_text, values)
            if not recent and not attention and not peer_replies and continuity is None:
                return None
            payload = {}
            if recent:
                payload["recent_group_activity"] = recent
            if attention:
                payload["attention"] = attention
            if peer_replies:
                payload["peer_replies"] = peer_replies
            if peer_repliers:
                payload["peer_repliers"] = peer_repliers
            if continuity is not None:
                payload["continuity"] = continuity
            return payload
        if context.scope.kind is ScopeKind.GROUP and self._private_contact(context, at, values):
            return {"private_contact": True}
        return None

    @staticmethod
    def _same_route(item, context):
        return (item["platform"] == context.scope.platform
                and item["account_id"] == (context.scope.account_id or ""))

    def _source_current(self, item, at):
        if item.get("source") is None or self.source_validator is None:
            return True
        reference = _source_ref(item["source"])
        if reference is None:
            return False
        try:
            return self.source_validator.validate_reference(reference, observed_at=at).is_success
        except Exception:
            return False

    def _continuity(self, document, context, at, current_text, values):
        """Match a private follow-up to the same person's recent group question.

        The capsule is intentionally narrow: only the current person's own public
        group wording and this role's own reply are eligible.  The match is used
        as conversational continuity, never as authorization or a new fact.
        """
        if values.get("person.cross_window_continuity_enabled", True) is False:
            return None
        current = _normalized_topic(current_text)
        if not current:
            return None
        minutes = self._integer(values, "person.cross_window_continuity_minutes", 30, 5, 180)
        cutoff = at - timedelta(minutes=minutes)
        subject = context.actor.subject.subject_id
        timeline = []
        for index, item in enumerate(document["events"]):
            when = _when(item["at"])
            if when is None or when < cutoff or when > at or not self._same_route(item, context):
                continue
            if item["window"] != "group" or item["kind"] not in ("spoke", "reply"):
                continue
            if item["kind"] == "spoke" and not self._source_current(item, at):
                continue
            timeline.append((when, index, item))
        timeline.sort(key=lambda row: (row[0], row[1]))

        if current in HANDOFF_CUES:
            # The user explicitly followed a group invitation into private chat.
            # Keep the same run's prior request, but only when the role's own
            # group reply actually invited them here; otherwise "来了" is just
            # an ordinary greeting.
            for when, index, item in reversed(timeline):
                if item["kind"] != "spoke" or item["subject"] != subject or not item["topic"]:
                    continue
                prior_reply = ""
                for later_at, later_index, later in timeline:
                    if later_index <= index:
                        continue
                    if later["group_id"] != item["group_id"]:
                        continue
                    if later["kind"] == "spoke":
                        break
                    if later["kind"] == "reply" and later["topic"]:
                        prior_reply = later["topic"]
                        break
                if prior_reply and any(marker in prior_reply for marker in HANDOFF_REPLY_MARKERS):
                    return {"state": "handoff",
                            "minutes_ago": max(0, int((at - when).total_seconds() // 60)),
                            "prior_question": item["topic"], "prior_reply": prior_reply}
            return None
        if len(current) < 4:
            return None
        best = None
        for when, index, item in timeline:
            if item["kind"] != "spoke" or item["subject"] != subject or not item["topic"]:
                continue
            score = _topic_similarity(current, item["topic"])
            if score < CONTINUITY_SIMILARITY:
                continue
            if best is None or score > best[0] or (score == best[0] and when > best[1]):
                best = (score, when, index, item)
        if best is None:
            return None

        _, question_at, question_index, question = best
        prior_reply = ""
        for when, index, item in timeline:
            if index <= question_index:
                continue
            if item["group_id"] != question["group_id"]:
                continue
            if item["kind"] == "spoke":
                # Another person spoke before the role answered; do not attribute
                # that later reply to this question.
                break
            if item["kind"] == "reply" and item["topic"]:
                prior_reply = item["topic"]
                break
        return {
            "state": "repeat",
            "minutes_ago": max(0, int((at - question_at).total_seconds() // 60)),
            "prior_question": question["topic"],
            "prior_reply": prior_reply,
        }

    def _recent_group_activity(self, document, context, at, *, hours=WINDOW_HOURS, limit=MAX_PROMPT_ITEMS):
        cutoff = at - timedelta(hours=hours)
        subject = context.actor.subject.subject_id
        items = []
        for item in document["events"]:
            when = _when(item["at"])
            if (item["window"] != "group" or item["subject"] != subject or not item["topic"]
                    or not self._same_route(item, context)
                    or when is None or when < cutoff or when > at
                    or not self._source_current(item, at)):
                continue
            items.append({"at": item["at"], "group_id": item["group_id"], "topic": item["topic"],
                          "peers": item["peer_count"]})
        items.sort(key=lambda item: item["at"])
        return items[-limit:]

    def _peer_replies(self, document, context, at, *, minutes=60):
        cutoff = at - timedelta(minutes=minutes)
        subject = context.actor.subject.subject_id
        count, speakers = 0, set()
        for item in document["events"]:
            when = _when(item["at"])
            if (item["kind"] != "peer_reply" or item["subject"] != subject
                    or not self._same_route(item, context)
                    or when is None or when < cutoff or when > at
                    or not self._source_current(item, at)):
                continue
            count += 1
            if item["speaker_account"]:
                speakers.add(item["speaker_account"])
        return min(64, count), min(64, len(speakers))

    @staticmethod
    def _attention(document, context, at, *, minutes_limit=ATTENTION_MINUTES):
        """The current person's own last window, never another member's activity."""
        if minutes_limit <= 0:
            return {}
        subject = context.actor.subject.subject_id
        latest = None
        for item in document["events"]:
            if item["subject"] != subject or not RoleAwareness._same_route(item, context):
                continue
            when = _when(item["at"])
            if when is None or when > at:
                continue
            if latest is None or when > latest[0]:
                latest = (when, item["window"])
        if latest is None or latest[1] != "group":
            return {}
        minutes = max(0, int((at - latest[0]).total_seconds() // 60))
        if minutes > minutes_limit:
            return {}
        return {"last_inbound_window": "group", "minutes_ago": minutes}

    def _private_contact(self, context, at, values=None) -> bool:
        values = self._values(context) if values is None else values
        if values.get("person.cross_window_group_identity_enabled", True) is False:
            return False
        reader = getattr(self.acquaintance, "private_contact", None)
        if not callable(reader):
            return False
        try:
            return bool(reader(context, at))
        except Exception:
            return False


__all__ = ["RoleAwareness"]
