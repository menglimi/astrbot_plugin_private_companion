"""Persistence for the role-level cross-window presence document."""

from __future__ import annotations

import json

from companion_v2.execution.models import encode, stamp

from .models import (MAX_DOCUMENT_CHARS, PREFIX, empty_document, event_record, owner_key, peer_reply_record,
                     prune, reply_record, role_ref, together_record, valid_document, window_of)


class RoleAwarenessStore:
    """One bounded metadata document per role, on the shared runtime metadata table.

    The caller owns the transaction.  A malformed or foreign document is treated
    as absent, so a damaged optional record can never block a reply or leak
    another role's windows.
    """

    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def key(context) -> str:
        return PREFIX + owner_key(context)

    def _decode(self, value, context):
        try:
            document = json.loads(value)
        except (TypeError, ValueError):
            return None
        if not valid_document(document) or document["role"] != role_ref(context):
            return None
        return document

    def read(self, context):
        row = self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?", (self.key(context),))
        return None if row is None else self._decode(row["value"], context)

    @staticmethod
    def _load(connection, context):
        row = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key = ?", (RoleAwarenessStore.key(context),)).fetchone()
        if row is None:
            return None
        try:
            document = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return document if valid_document(document) and document["role"] == role_ref(context) else None

    def write(self, connection, context, document):
        while len(encode(document)) > MAX_DOCUMENT_CHARS and document["events"]:
            document["events"] = document["events"][1:]
        connection.execute(
            """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (self.key(context), encode(document)))

    def observe_input(self, connection, context, at, text, event_key="", source=None, peer_count=0,
                      quote_sender_ids=()):
        """Record one admitted text input; duplicate event keys stay idempotent."""
        document = self._load(connection, context) or empty_document(context, at)
        key = str(event_key or "")[:256]
        if key and any(item["event_key"] == key for item in document["events"]):
            return document
        events = list(document["events"])
        spoke = event_record(context, at, text, key, source, peer_count)
        peer_events = []
        quoted = {str(item) for item in quote_sender_ids if str(item)}
        if context.scope.kind is not None and quoted and spoke["window"] == "group":
            subjects = []
            for item in events:
                if (item["kind"] != "spoke" or item["window"] != "group"
                        or item["group_id"] != spoke["group_id"]
                        or item["speaker_account"] not in quoted
                        or item["subject"] == spoke["subject"]):
                    continue
                if item["subject"] not in subjects:
                    subjects.append(item["subject"])
            for subject in subjects[:8]:
                peer_events.append(peer_reply_record(
                    context, at, subject, key + ":peer_reply:" + subject, source))
        events = prune((*events, spoke, *peer_events), at)
        attention = dict(document["attention"])
        attention.update(last_inbound_window=window_of(context), last_inbound_at=stamp(at))
        document = {**document, "events": events, "attention": attention, "updated_at": stamp(at)}
        self.write(connection, context, document)
        return document

    def observe_reply(self, connection, context, at, text: str = "", event_key: str = ""):
        """Record that the role itself sent something, with a bounded group reply topic."""
        document = self._load(connection, context) or empty_document(context, at)
        events = list(document["events"])
        key = str(event_key or "")[:256]
        if window_of(context) == "group" and text:
            duplicate = key and any(
                item["kind"] == "reply" and item["event_key"] == key for item in events)
            if not duplicate:
                events = prune((*events, reply_record(context, at, text, key)), at)
        attention = dict(document["attention"])
        attention.update(last_reply_window=window_of(context), last_reply_at=stamp(at))
        document = {**document, "events": events, "attention": attention, "updated_at": stamp(at)}
        self.write(connection, context, document)
        return document

    def observe_together_experience(self, connection, context, at, text: str, event_key: str = ""):
        """Record one private-only desensitized Together room summary."""
        if window_of(context) != "private":
            return None
        document = self._load(connection, context) or empty_document(context, at)
        key = str(event_key or "")[:256]
        events = list(document["events"])
        if key and any(item["kind"] == "together_room" and item["event_key"] == key for item in events):
            return document
        record = together_record(context, at, text, key)
        events = prune((*events, record), at)
        document = {**document, "events": events, "updated_at": stamp(at)}
        self.write(connection, context, document)
        return document


__all__ = ["RoleAwarenessStore"]
