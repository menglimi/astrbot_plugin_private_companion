from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from agenda_disclosure_policy import AgendaDisclosurePolicy
from runtime_scene_resolver import RuntimeSceneResolver
from schedule_authority import (
    ScheduleAuthorityAdapter,
    TrustedScheduleRef,
    validate_structured_schedule_ref,
)


NOW = datetime.fromisoformat("2026-07-30T21:34:00+08:00")


def _adapter() -> ScheduleAuthorityAdapter:
    return ScheduleAuthorityAdapter(clock=lambda: NOW)


def _event(event_id: str = "event-1", revision: str = "1", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "authority_kind": "calendar",
        "event_id": event_id,
        "revision": revision,
        "timezone": "Asia/Shanghai",
        "updated_at": "2026-07-30T21:34:00+08:00",
        "effective_from": "2026-07-31T09:00:00+08:00",
        "effective_to": "2026-07-31T10:00:00+08:00",
    }
    payload.update(extra)
    return payload


def _entry(ref: TrustedScheduleRef, *, entry_id: str = "entry-1") -> dict[str, object]:
    return {
        "entry_id": entry_id,
        "title": "class",
        "source_kind": "planned",
        "status": "planned",
        **ref.to_plan_fields(),
        "actor_type": "bot",
        "subject_actor_id": "bot-1",
    }


def test_forged_trust_marker_without_structured_ref_is_rejected() -> None:
    policy = AgendaDisclosurePolicy(bot_id="bot-1")
    view = policy.build_view(
        {
            "entries": [
                {
                    "entry_id": "forged",
                    "title": "class",
                    "source_kind": "planned",
                    "status": "planned",
                    "authority_kind": "calendar",
                    "commitment_level": "confirmed",
                    "source_refs": ["model-ref"],
                    "source_refs_trusted": True,
                    "actor_type": "bot",
                    "subject_actor_id": "bot-1",
                    "start_at": "2026-07-31T09:00:00+08:00",
                    "end_at": "2026-07-31T10:00:00+08:00",
                }
            ]
        },
        NOW,
        "schedule_commitment",
    )
    assert not view.entries
    assert "missing_schedule_ref" in view.redactions[0]["reasons"]


def test_expired_permission_and_invalid_timezone_refs_are_not_confirmed() -> None:
    adapter = _adapter()
    valid = adapter.issue_or_update(
        _event(expires_at="2026-07-30T20:00:00+08:00"),
        "bot-1",
    )
    assert isinstance(valid, TrustedScheduleRef)
    policy = AgendaDisclosurePolicy(bot_id="bot-1", schedule_authority=adapter)
    expired = policy.build_view({"entries": [_entry(valid)]}, NOW, "schedule_commitment")
    assert not expired.entries
    assert "expired" in expired.redactions[0]["reasons"]

    denied = replace(valid, authorized=False)
    denied_view = policy.build_view({"entries": [_entry(denied, entry_id="denied")]}, NOW, "schedule_commitment")
    assert not denied_view.entries
    assert "invalid_schedule_ref" in denied_view.redactions[0]["reasons"] or "permission_denied" in denied_view.redactions[0]["reasons"]

    bad_tz = replace(valid, timezone="Not/AZone")
    bad_tz_view = policy.build_view({"entries": [_entry(bad_tz, entry_id="bad-tz")]}, NOW, "schedule_commitment")
    assert not bad_tz_view.entries

    forged_interval = replace(valid, effective_from="2026-07-31T13:00:00+08:00")
    assert adapter.verify(forged_interval, now=NOW) == "invalid"


def test_structured_gate_checks_ref_id_before_delegating_to_verifier() -> None:
    adapter = _adapter()
    ref = adapter.issue_or_update(_event(), "bot-1")
    assert isinstance(ref, TrustedScheduleRef)

    class PermissiveVerifier:
        def verify(self, _ref: object, *, now: datetime | None = None) -> str:
            return "valid"

    forged = ref.as_dict()
    forged["ref_id"] = "trusted_schedule:forged"
    status, reason = validate_structured_schedule_ref(
        forged,
        source_refs=[forged["ref_id"]],
        expected_authority="calendar",
        expected_subject="bot-1",
        now=NOW,
        adapter=PermissiveVerifier(),
    )
    assert status == "invalid"
    assert reason == "schedule_ref_id_mismatch"


def test_cancel_and_revision_only_leave_latest_valid_reference_visible() -> None:
    adapter = _adapter()
    first = adapter.issue_or_update(_event(event_id="same"), "bot-1")
    assert isinstance(first, TrustedScheduleRef)
    second = adapter.issue_or_update(
        _event(
            event_id="same",
            revision="2",
            updated_at="2026-07-30T21:35:00+08:00",
            effective_from="2026-07-31T11:00:00+08:00",
            effective_to="2026-07-31T12:00:00+08:00",
        ),
        "bot-1",
    )
    assert isinstance(second, TrustedScheduleRef)
    policy = AgendaDisclosurePolicy(bot_id="bot-1", schedule_authority=adapter)
    view = policy.build_view({"entries": [_entry(first, entry_id="old"), _entry(second, entry_id="new")]}, NOW, "schedule_commitment")
    assert [item["entry_id"] for item in view.entries] == ["new"]
    cancelled = adapter.revoke_or_reschedule("same", "2", "cancelled", NOW)
    assert isinstance(cancelled, TrustedScheduleRef)
    assert cancelled.revision != second.revision
    assert cancelled.state == "cancelled"
    assert adapter.verify(second, now=NOW) == "revoked"
    assert adapter.verify(cancelled, now=NOW) == "cancelled"


def test_user_confirmation_requires_structured_proposition_actor_and_time() -> None:
    adapter = _adapter()
    base = _event(authority_kind="user_confirmation", event_id="confirm")
    assert not adapter.issue_or_update({**base, "confirmation_event_id": "chat-only"}, "bot-1")
    result = adapter.issue_or_update(
        {
            **base,
            "confirmation_event_id": "message-1",
            "confirmation_actor_id": "user-1",
            "proposition": "Bot 明早九点有课",
            "confirmed_at": "2026-07-30T21:34:00+08:00",
        },
        "bot-1",
    )
    assert isinstance(result, TrustedScheduleRef)


def test_schedule_target_binding_is_checked_against_disclosure_user() -> None:
    adapter = _adapter()
    ref = adapter.issue_or_update(_event(event_id="private-event"), "bot-1", target_user_id="user-1")
    assert isinstance(ref, TrustedScheduleRef)
    policy = AgendaDisclosurePolicy(
        bot_id="bot-1",
        target_user_id="user-2",
        schedule_authority=adapter,
    )
    view = policy.build_view({"entries": [_entry(ref)]}, NOW, "schedule_commitment")
    assert not view.entries
    assert "schedule_target_mismatch" in view.redactions[0]["reasons"]


def test_self_state_is_current_only_and_cannot_claim_external_results() -> None:
    resolver = RuntimeSceneResolver(bot_id="bot-1", clock=lambda: NOW, default_ttl_seconds=120)
    state = resolver.commit("在休息", now=NOW, window_id="w", origin_refs=["resolver:w"])
    assert state is not None
    assert state["status"] == "planned"
    assert state["idempotency_key"] == "runtime_commit:bot-1:w:1"
    assert resolver.commit("付款", now=NOW, window_id="w") is None
    assert resolver.commit("在休息", now=NOW - timedelta(minutes=1), window_id="w") is None
    assert resolver.commit("准备出门", now=NOW, window_id="w", expected_version=0) is None

    policy = AgendaDisclosurePolicy(bot_id="bot-1")
    current = policy.build_view({"entries": [state]}, NOW, "current_fact")
    history = policy.build_view({"entries": [state]}, NOW + timedelta(minutes=3), "history_fact")
    memory = policy.build_view({"entries": [state]}, NOW, "memory_write")
    assert len(current.entries) == 1
    assert not history.entries
    assert not memory.entries
    forged = dict(state)
    forged["source_refs"] = ["external"]
    assert not policy.build_view({"entries": [forged]}, NOW, "current_fact").entries


def test_self_state_ttl_and_user_turn_override() -> None:
    resolver = RuntimeSceneResolver(bot_id="bot-1", clock=lambda: NOW, default_ttl_seconds=60)
    assert resolver.resolve_now([], conversation_state=False, now=NOW) is None
    resting = resolver.resolve_now(
        [
            {
                "actor_type": "bot",
                "subject_actor_id": "bot-1",
                "state": "在休息",
            }
        ],
        conversation_state=False,
        now=NOW,
    )
    assert resting is not None
    assert resolver.get_current(now=NOW + timedelta(seconds=61)) is None
    interrupted = resolver.resolve_now([], conversation_state={"last_user_message": "hello"}, now=NOW)
    assert interrupted is not None
    assert interrupted["state"] == "陪你聊天"
    assert interrupted["state_version"] == resting["state_version"] + 1
