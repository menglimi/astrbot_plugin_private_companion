# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from agenda_runtime import AgendaRuntimeMixin
from calendar_observer import extract_calendar_candidates, merge_calendar_observations


NOW = datetime.fromisoformat("2026-08-20T12:00:00+08:00")


def test_observer_keeps_future_tense_as_pending_and_parses_numeric_dates() -> None:
    candidate = extract_calendar_candidates(
        "明天去医院",
        now=NOW,
        source_user_id="u-1",
        source_message_id="m-1",
    )[0]
    assert candidate["lifecycle_state"] == "candidate"
    assert candidate["lifecycle_status"] == "pending_confirmation"
    assert candidate["calendar_effective"] is False
    assert candidate["start_date"] == "2026-08-21"

    numeric = extract_calendar_candidates(
        "8月25日去复诊",
        now=NOW,
        source_user_id="u-1",
        source_message_id="m-2",
    )[0]
    assert numeric["start_date"] == "2026-08-25"


def test_observer_does_not_treat_question_or_negation_as_confirmation() -> None:
    candidates = extract_calendar_candidates("明天去不去医院", now=NOW, source_user_id="u-1", source_message_id="question")
    assert candidates
    assert candidates[0]["lifecycle_state"] == "candidate"
    for text in ("明天不去医院",):
        candidates = extract_calendar_candidates(text, now=NOW, source_user_id="u-1", source_message_id=text)
        assert not any(item.get("lifecycle_state") in {"confirmed", "active"} for item in candidates)


def test_observer_confirmation_merges_and_promotes_without_duplicate_candidate() -> None:
    first = extract_calendar_candidates("明天去医院", now=NOW, source_user_id="u-1", source_message_id="m-1")
    merged, _, _ = merge_calendar_observations([], first, now=NOW)
    second = extract_calendar_candidates("确认了明天去医院", now=NOW, source_user_id="u-1", source_message_id="m-2")
    merged, audit, summary = merge_calendar_observations(
        merged,
        second,
        text="确认了明天去医院",
        now=NOW,
    )
    assert len(merged) == 1
    assert merged[0]["lifecycle_state"] == "confirmed"
    assert merged[0]["lifecycle_status"] == "confirmed"
    assert len(merged[0]["evidence"]) == 2
    assert summary["promoted"] == 1
    assert audit[0]["operation"] == "promoted"


def test_observer_negation_resolves_existing_candidate() -> None:
    first = extract_calendar_candidates("明天去医院", now=NOW, source_user_id="u-1", source_message_id="m-1")
    merged, _, _ = merge_calendar_observations([], first, now=NOW)
    merged, audit, summary = merge_calendar_observations(
        merged,
        [],
        text="明天不去医院",
        now=NOW,
    )
    assert merged[0]["lifecycle_state"] == "cancelled"
    assert merged[0]["lifecycle_status"] == "cancelled"
    assert summary["resolved"] == 1
    assert audit[-1]["operation"] == "cancelled"


def test_short_confirmation_uses_the_latest_pending_candidate() -> None:
    first = extract_calendar_candidates("明天去医院", now=NOW, source_user_id="u-1", source_message_id="m-1")
    merged, _, _ = merge_calendar_observations([], first, now=NOW)
    merged, audit, _ = merge_calendar_observations(merged, [], text="确认了", now=NOW)
    assert merged[0]["lifecycle_state"] == "confirmed"
    assert audit[-1]["operation"] == "promoted"


def test_observer_replay_with_same_source_ref_is_idempotent() -> None:
    first = extract_calendar_candidates("明天去医院", now=NOW, source_user_id="u-1", source_message_id="same-message")
    replay = extract_calendar_candidates("明天去医院", now=NOW.replace(minute=13), source_user_id="u-1", source_message_id="same-message")
    merged, _, _ = merge_calendar_observations([], first, now=NOW)
    merged, _, summary = merge_calendar_observations(merged, replay, now=NOW.replace(minute=13))
    assert len(merged) == 1
    assert len(merged[0]["evidence"]) == 1
    assert summary["changed"] is False


class _Host(AgendaRuntimeMixin):
    calendar_timezone = "Asia/Shanghai"

    def __init__(self) -> None:
        self.data = {}
        self._schedule_data_save = lambda **kwargs: None

    def _calendar_now(self) -> datetime:
        return NOW


def test_runtime_candidate_stays_out_of_formal_calendar_until_confirmation() -> None:
    host = _Host()
    result = host._agenda_observe_calendar_message(
        text="明天去医院",
        event_time=NOW,
        source_ref="m-1",
        source_user_id="u-1",
        target_user_id="u-1",
    )
    assert result["candidates"]
    assert host.data["calendar_candidates"]
    assert host.data["calendar_events"] == []

    result = host._agenda_observe_calendar_message(
        text="确认了明天去医院",
        event_time=NOW,
        source_ref="m-2",
        source_user_id="u-1",
        target_user_id="u-1",
    )
    assert result["materialized"]
    assert len(host.data["calendar_events"]) == 1
    assert host.data["calendar_candidates"][0]["lifecycle_state"] == "confirmed"

    result = host._agenda_observe_calendar_message(
        text="明天不去医院",
        event_time=NOW,
        source_ref="m-3",
        source_user_id="u-1",
        target_user_id="u-1",
    )
    assert result["candidates"][0]["lifecycle_state"] == "cancelled"
    assert host.data["calendar_events"][0]["status"] == "cancelled"


def test_materializing_confirmed_candidate_surfaces_calendar_write_failure() -> None:
    host = _Host()
    candidate = {
        "candidate_id": "candidate-write-failure",
        "lifecycle_state": "confirmed",
        "updated_at": NOW.isoformat(),
        "proposed_record": {
            "calendar_id": "event-write-failure",
            "kind": "event",
            "title": "去医院",
            "start_date": "2026-08-21",
        },
    }

    def fail_upsert(_record: dict) -> dict:
        raise RuntimeError("injected calendar persistence failure")

    host._agenda_upsert_calendar_record = fail_upsert

    try:
        host._agenda_materialize_calendar_candidate(candidate)
    except RuntimeError as exc:
        assert str(exc) == "injected calendar persistence failure"
    else:
        raise AssertionError("calendar persistence failure was silently swallowed")


def test_pending_candidate_does_not_write_but_confirmation_surfaces_save_failure() -> None:
    host = _Host()
    save_calls: list[set[str]] = []

    def save(*, sections: set[str]) -> None:
        save_calls.append(set(sections))
        if "calendar_events" in sections:
            raise OSError("injected durable store failure")

    host._schedule_data_save = save
    pending = host._agenda_observe_calendar_message(
        text="明天去医院",
        event_time=NOW,
        source_ref="failure-m-1",
        source_user_id="u-1",
        target_user_id="u-1",
    )
    assert pending["candidates"]
    assert save_calls == [{"calendar_candidates"}]

    try:
        host._agenda_observe_calendar_message(
            text="确认了明天去医院",
            event_time=NOW,
            source_ref="failure-m-2",
            source_user_id="u-1",
            target_user_id="u-1",
        )
    except OSError as exc:
        assert str(exc) == "injected durable store failure"
    else:
        raise AssertionError("confirmed calendar save failure was silently swallowed")
