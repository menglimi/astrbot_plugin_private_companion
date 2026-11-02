"""The Laya ledger is a bypass, not a place where a write may vanish silently.

A rejected ``laya_decision_events`` row must say which constraint refused it, and
the diagnostic documents must be bounded to the column widths the schema declares
instead of being refused whole.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from companion_v2.decision.service import LayaDecisionService
from companion_v2.decision.store import LayaSettingsStore
from companion_v2.storage.runtime import StorageRuntime


class Refusing:
    """A transaction stand-in whose write is rejected by the database."""

    def __init__(self, error):
        self.error = error

    def __enter__(self):
        raise self.error

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def ledger(tmp_path):
    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    status = store.read()
    store.update(status["revision"], {"enabled": True})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-ledger-test")
    service = LayaDecisionService(store, runtime=runtime, origin_key="role-key")
    service.set_trace(event_ref="reply-" + "d" * 32, source="conversation")
    try:
        yield service, runtime
    finally:
        runtime.close()


def test_rejected_row_names_the_constraint_instead_of_only_its_type(ledger, monkeypatch, caplog):
    service, runtime = ledger
    error = sqlite3.IntegrityError("CHECK constraint failed: mode IN ('local', 'remote')")
    monkeypatch.setattr(runtime, "transaction", lambda **options: Refusing(error))
    with caplog.at_level(logging.WARNING):
        service._record(service.store.read(), "skipped", 0, reason_code="laya_disabled")
    recorded = caplog.text
    assert "code=IntegrityError" in recorded
    assert "CHECK constraint failed: mode IN ('local', 'remote')" in recorded


def test_non_database_failures_keep_their_type_without_quoting_the_row(ledger, monkeypatch, caplog):
    service, runtime = ledger
    monkeypatch.setattr(runtime, "transaction",
                        lambda **options: Refusing(ValueError("用户私密内容被回显")))
    with caplog.at_level(logging.WARNING):
        service._record(service.store.read(), "skipped", 0, reason_code="laya_disabled")
    recorded = caplog.text
    assert "code=ValueError" in recorded
    assert "用户私密内容" not in recorded


def test_oversized_decision_document_is_bounded_instead_of_dropped(ledger):
    service, runtime = ledger
    decision = {"action": "reply", "reply_intent": "answer", "reply_style": "normal",
                "reply_form": "text", "segment_mode": "single", "tool_need": "none",
                "turn_state": "complete", "reason_codes": ["x" * 5000]}
    service._record(service.store.read(), "ok", 12, decision=decision)
    stored = runtime.read_one("SELECT decision_json FROM laya_decision_events", ())
    assert stored is not None
    document = json.loads(stored["decision_json"])
    assert document["truncated"] is True
    assert len(stored["decision_json"]) <= 4096


def test_decision_documents_within_the_column_width_are_stored_verbatim(ledger):
    service, runtime = ledger
    decision = {"action": "reply", "reason_codes": ["a"]}
    service._record(service.store.read(), "ok", 12, decision=decision)
    stored = runtime.read_one("SELECT decision_json, enabled_fields_json FROM laya_decision_events", ())
    assert json.loads(stored["decision_json"]) == decision
    assert sorted(json.loads(stored["enabled_fields_json"])) == sorted(service.store.read()["decision_fields"])
