# -*- coding: utf-8 -*-
"""Pure local-data runtime mixin for the chat-side C3 agenda."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

try:
    from .activity_capture import ActivityCapture
    from .agenda_contracts import (
        SCHEDULE_WINDOWS,
        interval_overlaps_window,
        migrate_store,
        normalize_observed_activity,
        normalize_plan_item,
        normalize_reconciliation,
        normalize_window_snapshot,
        stable_id,
        timezone_or_default,
        window_bounds,
    )
    from .calendar_contracts import (
        advance_calendar_lifecycle,
        calendar_records_from_store,
        normalize_calendar_record,
        resolve_calendar_timeline,
        resolve_calendar_snapshot,
    )
    from .calendar_observer import extract_calendar_candidates, merge_calendar_observations
    from .schedule_reconciler import reconcile
    from .unified_agenda import build_unified_agenda, format_agenda_context
    from .agenda_disclosure_policy import AgendaDisclosurePolicy
    from .runtime_scene_resolver import RuntimeSceneResolver
except ImportError:
    from activity_capture import ActivityCapture
    from agenda_contracts import (
        SCHEDULE_WINDOWS,
        interval_overlaps_window,
        migrate_store,
        normalize_observed_activity,
        normalize_plan_item,
        normalize_reconciliation,
        normalize_window_snapshot,
        stable_id,
        timezone_or_default,
        window_bounds,
    )
    from calendar_contracts import (
        advance_calendar_lifecycle,
        calendar_records_from_store,
        normalize_calendar_record,
        resolve_calendar_timeline,
        resolve_calendar_snapshot,
    )
    from calendar_observer import extract_calendar_candidates, merge_calendar_observations
    from schedule_reconciler import reconcile
    from unified_agenda import build_unified_agenda, format_agenda_context
    from agenda_disclosure_policy import AgendaDisclosurePolicy
    from runtime_scene_resolver import RuntimeSceneResolver


class AgendaRuntimeMixin:
    """Keep C3 state in ``self.data`` and nowhere else."""

    def _agenda_timezone_name(self) -> str:
        getter = getattr(self, "_calendar_timezone_name", None)
        if callable(getter):
            try:
                return str(getter() or "Asia/Shanghai")
            except Exception:
                pass
        return str(getattr(self, "calendar_timezone", "Asia/Shanghai") or "Asia/Shanghai")

    def _agenda_now(self) -> datetime:
        getter = getattr(self, "_calendar_now", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        return datetime.now().astimezone()

    def _agenda_prepare_store(self) -> None:
        if not isinstance(getattr(self, "data", None), dict):
            self.data = {}
        migrated, changed = migrate_store(self.data)
        self.data = migrated
        if changed:
            self._agenda_migration_dirty = True
        if not isinstance(getattr(self, "_agenda_capture", None), ActivityCapture):
            self._agenda_capture = ActivityCapture()
        bot_id = str(
            getattr(self, "bot_id", "")
            or getattr(self, "bot_personal_subject", "")
            or "bot_self"
        ).strip()
        timezone_name = self._agenda_timezone_name()
        policy = getattr(self, "_agenda_disclosure_policy", None)
        if not isinstance(policy, AgendaDisclosurePolicy) or policy.bot_id != bot_id or policy.timezone_name != timezone_name:
            self._agenda_disclosure_policy = AgendaDisclosurePolicy(bot_id=bot_id, timezone_name=timezone_name)
        runtime_resolver = getattr(self, "_runtime_scene_resolver", None)
        if (
            not isinstance(runtime_resolver, RuntimeSceneResolver)
            or runtime_resolver.bot_id != bot_id
            or getattr(runtime_resolver, "timezone_name", timezone_name) != timezone_name
        ):
            self._runtime_scene_resolver = RuntimeSceneResolver(
                bot_id=bot_id,
                clock=self._agenda_now,
                timezone_name=timezone_name,
            )

    def _agenda_activities_store(self) -> list[dict[str, Any]]:
        self._agenda_prepare_store()
        return self.data["observed_activities"]

    def _agenda_snapshots_store(self) -> list[dict[str, Any]]:
        self._agenda_prepare_store()
        return self.data["window_snapshots"]

    def _agenda_reconciliation_store(self) -> list[dict[str, Any]]:
        self._agenda_prepare_store()
        return self.data["agenda_reconciliation_history"]

    def _agenda_calendar_records_store(self) -> list[dict[str, Any]]:
        """Return durable calendar records across the additive storage sections."""

        self._agenda_prepare_store()
        return calendar_records_from_store(self.data)

    def _agenda_calendar_candidates_store(self) -> list[dict[str, Any]]:
        """Return pending calendar proposals without exposing them as facts."""

        self._agenda_prepare_store()
        values = self.data.get("calendar_candidates")
        if not isinstance(values, list):
            values = []
            self.data["calendar_candidates"] = values
        return values

    def _agenda_materialize_calendar_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Promote one confirmed proposal into the formal calendar sections."""

        if not isinstance(candidate, dict):
            return None
        state = str(candidate.get("lifecycle_state") or candidate.get("lifecycle") or "candidate").lower()
        if state not in {"confirmed", "active"}:
            return None
        proposed = candidate.get("proposed_record") if isinstance(candidate.get("proposed_record"), dict) else {}
        if not proposed:
            proposed = {
                key: deepcopy(value)
                for key, value in candidate.items()
                if key not in {
                    "candidate_id", "lifecycle_status", "observation_intent", "confirmation_requested",
                    "source_excerpt", "source_message_at", "conversation_id", "target_user_id",
                    "created_at", "updated_at", "expires_at", "decision_trace", "revision", "proposed_record",
                }
            }
        formal = deepcopy(proposed)
        formal["lifecycle_state"] = state
        formal["lifecycle"] = state
        formal["status"] = "active" if state == "active" else "confirmed"
        formal["commitment_level"] = "confirmed"
        formal["calendar_effective"] = True
        formal["source"] = "calendar_candidate_confirmation"
        formal["confirmed_candidate_id"] = str(candidate.get("candidate_id") or "")
        formal["confirmed_at"] = str(candidate.get("updated_at") or self._agenda_now().isoformat(timespec="seconds"))
        return self._agenda_upsert_calendar_record(formal)

    def _agenda_observe_calendar_message(
        self,
        *,
        text: str,
        event_time: datetime | float | int | None = None,
        source_ref: str = "",
        conversation_id: str = "",
        source_user_id: str = "",
        target_user_id: str = "",
        subject_actor_id: str = "bot_self",
    ) -> dict[str, Any]:
        """Observe a private message and persist only a candidate proposal.

        This path is deliberately fail-open for the chat pipeline.  A source
        message can be replayed safely because its evidence ID and candidate
        identity are stable; only explicit confirmation materializes a formal
        calendar row.
        """

        self._agenda_prepare_store()
        timezone_name = self._agenda_timezone_name()
        timezone = timezone_or_default(timezone_name)
        if isinstance(event_time, datetime):
            observed_at = event_time.astimezone(timezone) if event_time.tzinfo else event_time.replace(tzinfo=timezone)
        else:
            try:
                observed_at = datetime.fromtimestamp(float(event_time), tz=timezone) if event_time is not None else self._agenda_now()
            except (TypeError, ValueError, OSError, OverflowError):
                observed_at = self._agenda_now()
        try:
            # Expiry is a lifecycle transition, not deletion: retaining the
            # row and its evidence prevents an old candidate from resurfacing
            # as a fresh fact while keeping the audit trail intact.
            existing = self._agenda_calendar_candidates_store()
            for item in existing:
                if not isinstance(item, dict):
                    continue
                state = str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate")
                expires_at = str(item.get("expires_at") or "")
                if state in {"candidate", "tentative"} and expires_at:
                    try:
                        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if expires.tzinfo is None:
                            expires = expires.replace(tzinfo=timezone)
                        if expires <= observed_at:
                            transitioned = advance_calendar_lifecycle(item, "expired", now=observed_at, timezone_name=timezone_name)
                            for key in ("candidate_id", "proposed_record", "source_excerpt", "source_message_at", "conversation_id", "target_user_id", "created_at", "expires_at", "decision_trace", "revision"):
                                if key in item and key not in transitioned:
                                    transitioned[key] = deepcopy(item[key])
                            item.clear()
                            item.update(transitioned)
                            item["lifecycle_status"] = "expired"
                    except (TypeError, ValueError, ImportError):
                        continue
            extracted = extract_calendar_candidates(
                text,
                now=observed_at,
                timezone_name=timezone_name,
                subject_actor_id=subject_actor_id or "bot_self",
                source_user_id=source_user_id,
                source_message_id=source_ref,
                conversation_id=conversation_id,
                target_user_id=target_user_id or source_user_id,
            )
            merged, audit, summary = merge_calendar_observations(
                existing,
                extracted,
                text=text,
                now=observed_at,
                timezone_name=timezone_name,
            )
        except Exception:
            return {"changed": False, "changed_sections": set(), "candidates": [], "audit": [], "summary": {}}

        changed_sections: set[str] = set()
        if merged != existing:
            self.data["calendar_candidates"] = merged
            changed_sections.add("calendar_candidates")

        materialized: list[dict[str, Any]] = []
        for candidate in merged:
            if not isinstance(candidate, dict):
                continue
            state = str(candidate.get("lifecycle_state") or candidate.get("lifecycle") or "candidate")
            if state in {"cancelled", "completed", "expired"} and candidate.get("materialized_calendar_id"):
                if self._agenda_cancel_calendar_record(str(candidate.get("materialized_calendar_id") or "")):
                    changed_sections.update({"calendar_events", "calendar_rules", "calendar_exceptions"})
                continue
            if state not in {"confirmed", "active"} or candidate.get("materialized_calendar_id"):
                continue
            saved = self._agenda_materialize_calendar_candidate(candidate)
            if not saved:
                continue
            candidate["materialized_calendar_id"] = str(saved.get("calendar_id") or "")
            candidate["materialized_at"] = observed_at.isoformat(timespec="seconds")
            candidate["decision_trace"] = list(candidate.get("decision_trace") or []) + [{
                "operation": "materialized",
                "at": observed_at.isoformat(timespec="seconds"),
                "calendar_id": str(saved.get("calendar_id") or ""),
            }]
            candidate["revision"] = max(1, int(candidate.get("revision") or 1)) + 1
            changed_sections.add("calendar_candidates")
            materialized.append(deepcopy(saved))
        if changed_sections:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                try:
                    saver(sections=changed_sections)
                except TypeError:
                    saver(changed_sections)
        return {
            "changed": bool(changed_sections),
            "changed_sections": changed_sections,
            "candidates": deepcopy(merged),
            "audit": deepcopy(audit),
            "summary": deepcopy(summary),
            "materialized": materialized,
        }

    def _agenda_decide_calendar_candidate(
        self,
        candidate_id: str,
        action: str,
        *,
        source: str = "manual",
        note: str = "",
    ) -> dict[str, Any] | None:
        """Confirm or reject a pending candidate while retaining its evidence."""

        self._agenda_prepare_store()
        target = str(candidate_id or "").strip()
        if not target:
            return None
        candidates = self._agenda_calendar_candidates_store()
        item = next((row for row in candidates if isinstance(row, dict) and str(row.get("candidate_id") or row.get("calendar_id") or "") == target), None)
        if item is None:
            return None
        state = str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate")
        if state in {"completed", "cancelled", "expired"}:
            return deepcopy(item)
        now = self._agenda_now()
        evidence = {
            "source_type": "manual" if source == "manual" else "message",
            "source_id": target,
            "quote": _single_line(note or action, 240) if "_single_line" in globals() else str(note or action)[:240],
            "observed_at": now.isoformat(timespec="seconds"),
            "actor": source,
        }
        transitioned = advance_calendar_lifecycle(item, action, evidence=evidence, now=now, timezone_name=self._agenda_timezone_name())
        for key in ("candidate_id", "proposed_record", "source_excerpt", "source_message_at", "conversation_id", "target_user_id", "created_at", "expires_at", "decision_trace", "materialized_calendar_id", "materialized_at"):
            if key in item and key not in transitioned:
                transitioned[key] = deepcopy(item[key])
        item.clear()
        item.update(transitioned)
        item["decision_trace"] = list(item.get("decision_trace") or []) + [{"operation": action, "at": now.isoformat(timespec="seconds"), "source": source, "note": str(note or "")[:240]}]
        item["revision"] = max(1, int(item.get("revision") or 1)) + 1
        state = str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate")
        item["lifecycle_status"] = {"confirmed": "confirmed", "active": "active", "cancelled": "cancelled", "completed": "completed", "expired": "expired"}.get(state, "pending_confirmation")
        item["calendar_effective"] = state in {"confirmed", "active"}
        if state in {"confirmed", "active"}:
            self._agenda_materialize_calendar_candidate(item)
            item["materialized_calendar_id"] = str(item.get("calendar_id") or "")
            item["materialized_at"] = now.isoformat(timespec="seconds")
        elif state in {"cancelled", "completed", "expired"} and item.get("materialized_calendar_id"):
            self._agenda_cancel_calendar_record(str(item.get("materialized_calendar_id") or ""))
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            try:
                saver(sections={"calendar_candidates"})
            except TypeError:
                saver({"calendar_candidates"})
        return deepcopy(item)

    def _agenda_calendar_snapshot(
        self,
        date_key: str = "",
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve long-lived calendar constraints for one local date.

        This is intentionally separate from ``_agenda_build``: callers can
        consume durable periods/rules without treating them as a generated
        daily plan or execution evidence.
        """

        self._agenda_prepare_store()
        current = now or self._agenda_now()
        timezone = timezone_or_default(self._agenda_timezone_name())
        target = str(date_key or current.astimezone(timezone).date().isoformat())[:10]
        return resolve_calendar_snapshot(
            self._agenda_calendar_records_store(),
            target,
            timezone_name=self._agenda_timezone_name(),
        )

    def _agenda_calendar_timeline(
        self,
        date_key: str = "",
        *,
        now: datetime | None = None,
        history_days: int = 3,
        horizon_days: int = 14,
    ) -> dict[str, Any]:
        """Resolve the longitudinal life background used by companion features."""

        self._agenda_prepare_store()
        current = now or self._agenda_now()
        timezone = timezone_or_default(self._agenda_timezone_name())
        target = str(date_key or current.astimezone(timezone).date().isoformat())[:10]
        return resolve_calendar_timeline(
            self._agenda_calendar_records_store(),
            target,
            timezone_name=self._agenda_timezone_name(),
            history_days=history_days,
            horizon_days=horizon_days,
        )

    def _agenda_calendar_allows_item(self, item: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
        """Retain calendar rows as context; never delete plan prose by keywords.

        The disclosure policy decides whether a plan is an execution fact.  The
        calendar timeline supplies phase/rhythm context to the model, while an
        explicit user-confirmed cancellation remains handled by the calendar
        exception resolver itself.
        """

        return isinstance(item, dict) or item is None

    def _agenda_upsert_calendar_record(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize and persist a calendar record without touching daily plans."""

        self._agenda_prepare_store()
        normalized = normalize_calendar_record(
            raw,
            now=self._agenda_now(),
            timezone_name=self._agenda_timezone_name(),
        )
        kind = str(normalized.get("kind") or "event")
        section = "calendar_rules" if kind == "recurrence" else "calendar_exceptions" if kind == "exception" else "calendar_events"
        records = self.data.setdefault(section, [])
        if not isinstance(records, list):
            records = []
            self.data[section] = records
        record_id = str(normalized.get("calendar_id") or "")
        changed_sections = {section}
        for other_section in ("calendar_events", "calendar_rules", "calendar_exceptions"):
            if other_section == section:
                continue
            other_records = self.data.get(other_section)
            if isinstance(other_records, list):
                before = len(other_records)
                other_records[:] = [
                    item for item in other_records
                    if not (isinstance(item, dict) and str(item.get("calendar_id") or "") == record_id)
                ]
                if len(other_records) != before:
                    changed_sections.add(other_section)
        existing = next((item for item in records if isinstance(item, dict) and str(item.get("calendar_id") or "") == record_id), None)
        if existing is None:
            records.append(deepcopy(normalized))
        else:
            existing.update(deepcopy(normalized))
            normalized = deepcopy(existing)
        records[:] = records[-1000:]
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            try:
                saver(sections=changed_sections)
            except TypeError:
                saver(changed_sections)
        self._agenda_build_cache = None
        self._agenda_disclosure_cache = None
        return deepcopy(normalized)

    def _agenda_cancel_calendar_record(self, calendar_id: str) -> bool:
        """Cancel a record in-place, preserving its history for auditability."""

        self._agenda_prepare_store()
        target = str(calendar_id or "").strip()
        if not target:
            return False
        for section in ("calendar_events", "calendar_rules", "calendar_exceptions"):
            records = self.data.get(section)
            if not isinstance(records, list):
                continue
            for item in records:
                if isinstance(item, dict) and str(item.get("calendar_id") or "") == target:
                    item["status"] = "cancelled"
                    item["version"] = max(1, int(item.get("version") or 1)) + 1
                    saver = getattr(self, "_schedule_data_save", None)
                    if callable(saver):
                        try:
                            saver(sections={section})
                        except TypeError:
                            saver({section})
                    self._agenda_build_cache = None
                    self._agenda_disclosure_cache = None
                    return True
        return False

    def _agenda_capture_inbound_message(
        self,
        *,
        text: str,
        event_time: datetime,
        source_ref: str,
        conversation_id: str,
        participant: str = "user",
        message_count: int = 1,
        topic: str = "",
        visibility: str = "private",
    ) -> dict[str, Any] | None:
        self._agenda_prepare_store()
        candidate = self._agenda_capture.capture_message(
            text=text,
            event_time=event_time,
            source_ref=source_ref,
            conversation_id=conversation_id,
            participant=participant,
            message_count=message_count,
            topic=topic,
            visibility=visibility,
        )
        if candidate is None:
            return None
        activities = self._agenda_activities_store()
        activity_id = candidate.get("activity_id")
        existing = next((item for item in activities if item.get("activity_id") == activity_id), None)
        if existing is None:
            activities.append(deepcopy(candidate))
            result = candidate
        else:
            existing_refs = list(existing.get("source_refs") or [])
            for ref in candidate.get("source_refs") or []:
                if ref not in existing_refs:
                    existing_refs.append(ref)
            existing.update(deepcopy(candidate))
            existing["source_refs"] = existing_refs[:50]
            existing["version"] = int(existing.get("version") or 1) + 1
            result = deepcopy(existing)
        activities[:] = activities[-500:]
        return result

    def _agenda_capture_hard_fact(self, activity: dict[str, Any]) -> dict[str, Any]:
        self._agenda_prepare_store()
        payload = dict(activity or {})
        payload.setdefault("actor_type", "bot")
        payload.setdefault("subject_actor_id", self._agenda_disclosure_policy.bot_id)
        payload.setdefault("source_actor_id", "system")
        normalized = normalize_observed_activity(payload, now=self._agenda_now())
        activities = self._agenda_activities_store()
        existing = next((item for item in activities if item.get("activity_id") == normalized.get("activity_id")), None)
        if existing is None:
            activities.append(normalized)
            result = deepcopy(normalized)
        else:
            existing.update(deepcopy(normalized))
            existing["version"] = int(existing.get("version") or 1) + 1
            result = deepcopy(existing)
        activities[:] = activities[-500:]
        return result

    def _agenda_current_plan_items(self) -> list[dict[str, Any]]:
        self._agenda_prepare_store()
        plan = self.data.get("daily_plan") if isinstance(self.data.get("daily_plan"), dict) else {}
        plan_date = str(plan.get("date") or self._agenda_now().date().isoformat())[:10]
        items = plan.get("items") if isinstance(plan.get("items"), list) else []
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            item = deepcopy(raw)
            item.setdefault("date", plan_date)
            item.setdefault("subject_actor_id", getattr(getattr(self, "_agenda_disclosure_policy", None), "bot_id", "bot_self"))
            item.setdefault("actor_type", "bot")
            try:
                normalized = normalize_plan_item(item, plan_id=str(item.get("plan_id") or f"{plan_date}:{index}"), now=self._agenda_now())
            except Exception:
                continue
            if normalized.get("start_at") is None:
                clock = str(normalized.get("time") or normalized.get("start") or "").strip()
                if clock:
                    normalized["start_at"] = f"{plan_date}T{clock}:00" if len(clock) == 5 else f"{plan_date}T{clock}"
            if normalized.get("end_at") is None:
                end_clock = str(normalized.get("end") or normalized.get("end_time") or "").strip()
                if end_clock:
                    normalized["end_at"] = f"{plan_date}T{end_clock}:00" if len(end_clock) == 5 else f"{plan_date}T{end_clock}"
            result.append(normalized)
        return result

    def _agenda_cache_signature(self) -> tuple[Any, ...]:
        """Return a cheap signature for the stores feeding disclosure views.

        The signature deliberately tracks replacement, version, lifecycle and
        interval changes without serializing the full plan/raw model output.
        This keeps repeated consumers from re-normalizing unchanged stores
        while still invalidating on the edits that affect agenda eligibility.
        """

        self._agenda_prepare_store()
        plan = self.data.get("daily_plan") if isinstance(self.data.get("daily_plan"), dict) else {}
        plan_items = plan.get("items") if isinstance(plan.get("items"), list) else []
        plan_item_signature = tuple(
            (
                id(item),
                str(item.get("plan_id") or ""),
                item.get("version"),
                str(item.get("date") or ""),
                str(item.get("time") or item.get("start") or item.get("start_at") or ""),
                str(item.get("end") or item.get("end_time") or item.get("end_at") or ""),
                str(item.get("activity") or item.get("title") or ""),
                str(item.get("status") or ""),
                str(item.get("lifecycle_status") or ""),
                str(item.get("changed_at") or ""),
                str(item.get("evidence_kind") or ""),
                str(item.get("fact_eligibility") or ""),
                str(item.get("subject_actor_id") or ""),
            )
            for item in plan_items
            if isinstance(item, dict)
        )
        activities = self.data.get("observed_activities")
        activity_items = activities if isinstance(activities, list) else []
        activity_signature = tuple(
            (
                id(item),
                str(item.get("activity_id") or ""),
                item.get("version"),
                str(item.get("start_at") or ""),
                str(item.get("end_at") or ""),
                str(item.get("title") or item.get("summary") or ""),
                str(item.get("status") or ""),
                str(item.get("updated_at") or item.get("captured_at") or ""),
                str(item.get("evidence_kind") or ""),
                str(item.get("subject_actor_id") or ""),
            )
            for item in activity_items
            if isinstance(item, dict)
        )
        calendar_sections = tuple(
            (
                section,
                id(self.data.get(section)),
                len(self.data.get(section) or []) if isinstance(self.data.get(section), list) else -1,
                tuple(
                    (
                        str(item.get("calendar_id") or ""),
                        int(item.get("version") or 1) if isinstance(item, dict) and str(item.get("version") or "").strip().lstrip("-").isdigit() else 1,
                        str(item.get("status") or ""),
                        str(item.get("start_date") or item.get("date") or ""),
                        str(item.get("end_date") or item.get("until") or ""),
                        str(item.get("title") or ""),
                        str(item.get("start_time") or ""),
                        str(item.get("end_time") or ""),
                        str(item.get("frequency") or ""),
                        str(item.get("interval") or ""),
                        tuple(sorted(item.get("by_weekday") or [], key=str)) if isinstance(item.get("by_weekday"), set) else tuple(item.get("by_weekday") or []) if isinstance(item.get("by_weekday"), (list, tuple)) else str(item.get("by_weekday") or ""),
                        tuple(sorted(item.get("by_monthday") or [], key=str)) if isinstance(item.get("by_monthday"), set) else tuple(item.get("by_monthday") or []) if isinstance(item.get("by_monthday"), (list, tuple)) else str(item.get("by_monthday") or ""),
                        str(item.get("count") or ""),
                        str(item.get("until") or ""),
                        repr(item.get("all_day")),
                        str(item.get("timezone") or ""),
                        str(item.get("target_id") or ""),
                        str(item.get("action") or ""),
                        str(item.get("new_date") or ""),
                        str(item.get("priority") or ""),
                    )
                    for item in (self.data.get(section) if isinstance(self.data.get(section), list) else [])
                    if isinstance(item, dict)
                ),
            )
            for section in ("calendar_events", "calendar_rules", "calendar_exceptions")
        )
        return (
            id(plan),
            str(plan.get("date") or ""),
            str(plan.get("generated_at") or ""),
            len(plan_items),
            plan_item_signature,
            id(activities),
            len(activity_items),
            activity_signature,
            calendar_sections,
        )

    def _agenda_build(self, *, date_key: str = "", now: datetime | None = None) -> dict[str, Any]:
        self._agenda_prepare_store()
        current = now or self._agenda_now()
        cache_key = (
            self._agenda_cache_signature(),
            current.isoformat(timespec="minutes"),
            str(date_key or ""),
            self._agenda_timezone_name(),
        )
        cached = getattr(self, "_agenda_build_cache", None)
        if isinstance(cached, dict) and cached.get("key") == cache_key:
            return deepcopy(cached.get("agenda") or {})
        agenda = build_unified_agenda(
            plans=self._agenda_current_plan_items(),
            activities=self._agenda_activities_store(),
            now=current,
            date_key=date_key,
            timezone_name=self._agenda_timezone_name(),
        )
        self._agenda_build_cache = {"key": cache_key, "agenda": deepcopy(agenda)}
        return agenda

    def _agenda_disclosure_view(
        self,
        purpose: str = "future_schedule",
        *,
        now: datetime | None = None,
        target_user_id: str = "",
        max_entries: int = 32,
        date_key: str = "",
    ) -> dict[str, Any]:
        """Return the only agenda view that should cross a module boundary."""

        self._agenda_prepare_store()
        current = now or self._agenda_now()
        minute_key = current.isoformat(timespec="minutes")
        cache_key = (
            self._agenda_cache_signature(),
            minute_key,
            str(purpose or "future_schedule"),
            str(target_user_id or ""),
            int(max_entries),
            str(date_key or ""),
            self._agenda_timezone_name(),
        )
        cached = getattr(self, "_agenda_disclosure_cache", None)
        if isinstance(cached, dict) and cached.get("key") == cache_key:
            return deepcopy(cached.get("view") or {})

        agenda = self._agenda_build(date_key=str(date_key or ""), now=current)
        view = self._agenda_disclosure_policy.build_view(
            agenda,
            now=current,
            purpose=purpose,
            target_user_id=target_user_id,
            max_entries=max_entries,
        )
        self._agenda_disclosure_cache = {"key": cache_key, "view": deepcopy(view)}
        return view

    def _agenda_runtime_scene(
        self,
        *,
        conversation_state: Any = None,
        now: datetime | None = None,
        hard_constraints: Any = None,
    ) -> dict[str, Any] | None:
        """Resolve a short-lived Bot-only current state without mutating plans."""

        self._agenda_prepare_store()
        agenda = self._agenda_build(now=now or self._agenda_now())
        # A clock window or a soft plan is not a runtime state.  The resolver
        # may only consume entries that the disclosure layer has already
        # qualified as a Bot current fact.  This prevents ordinary planned
        # activity text (for example, "上课" or "出门") from becoming a
        # short-lived ``self_state_commit`` merely because its time arrived.
        bot_id = str(getattr(self._agenda_disclosure_policy, "bot_id", "bot_self") or "bot_self")
        candidates: list[dict[str, Any]] = []
        for item in (
            list(agenda.get("current_fact") or [])
            + list(agenda.get("plans") or [])
        ):
            if not isinstance(item, dict):
                continue
            if str(item.get("subject_actor_id") or "") != bot_id:
                continue
            phase = str(item.get("temporal_phase") or "").lower()
            eligibility = str(item.get("fact_eligibility") or "").lower()
            if phase != "current" or eligibility not in {"current_internal", "current_observed"}:
                continue
            if not self._agenda_calendar_allows_item(item, now=now or self._agenda_now()):
                continue
            candidates.append(item)
        return self._runtime_scene_resolver.resolve_now(
            candidates,
            conversation_state=conversation_state,
            hard_constraints=hard_constraints,
            now=now or self._agenda_now(),
        )

    @staticmethod
    def _agenda_clock_from_value(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) >= 16 and "T" in text:
            return text.split("T", 1)[1][:5]
        return text[:5] if len(text) >= 5 and text[2:3] == ":" else ""

    def _agenda_current_context_item(
        self,
        *,
        conversation_state: Any = None,
        now: datetime | None = None,
        hard_constraints: Any = None,
    ) -> dict[str, Any] | None:
        """Return a Bot-only current item for behavior and scene consumers.

        The result is either an evidence-backed ``current_fact`` or a
        short-lived runtime commit.  Raw plan prose and future commitments are
        deliberately never returned from this boundary.
        """

        current = now or self._agenda_now()
        view = self._agenda_disclosure_view("current_fact", now=current, max_entries=32)
        entries = getattr(view, "entries", None)
        if entries is None and hasattr(view, "get"):
            try:
                entries = view.get("entries", [])
            except Exception:
                entries = []
        bot_id = str(getattr(self._agenda_disclosure_policy, "bot_id", "bot_self") or "bot_self")
        eligible = [
            item
            for item in entries
            if isinstance(item, dict)
            and str(item.get("subject_actor_id") or "") == bot_id
            and str(item.get("temporal_phase") or "").lower() == "current"
            and str(item.get("fact_eligibility") or "").lower() in {"current_internal", "current_observed"}
            and self._agenda_calendar_allows_item(item, now=current)
        ]
        if eligible:
            selected = sorted(
                eligible,
                key=lambda item: (
                    str(item.get("fact_eligibility") or "") != "current_observed",
                    str(item.get("start_at") or item.get("committed_at") or ""),
                ),
            )[0]
            title = str(selected.get("title") or selected.get("state") or selected.get("activity") or "").strip()[:120]
            return {
                **deepcopy(selected),
                "time": str(selected.get("time") or self._agenda_clock_from_value(selected.get("start_at") or selected.get("committed_at")))[:12],
                "end": str(selected.get("end") or self._agenda_clock_from_value(selected.get("end_at") or selected.get("valid_until")))[:12],
                "activity": title,
                "title": title,
                "message_seed": "",
            }

        runtime = self._agenda_runtime_scene(
            conversation_state=conversation_state,
            hard_constraints=hard_constraints,
            now=current,
        )
        if not isinstance(runtime, dict):
            return None
        title = str(runtime.get("state") or runtime.get("title") or "").strip()[:120]
        if not title:
            return None
        return {
            **deepcopy(runtime),
            "time": self._agenda_clock_from_value(runtime.get("committed_at")),
            "end": self._agenda_clock_from_value(runtime.get("valid_until")),
            "activity": title,
            "title": title,
            "mood": "当前状态",
            "message_seed": "",
        }

    def _agenda_current_interruption_context(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return a tentative chat-overlap hint without promoting execution."""

        current = now or self._agenda_now()
        agenda = self._agenda_build(now=current)
        candidates = [
            item
            for item in agenda.get("reconciliation_candidates", [])
            if isinstance(item, dict) and item.get("status") == "possible_interruption"
        ]
        if not candidates:
            return None
        selected = candidates[0]
        plan_title = str(selected.get("plan_title") or "").strip()[:120] or "原定日程"
        activity_summary = str(selected.get("activity_summary") or "").strip()[:160] or "一段持续聊天"
        return {
            "active": True,
            "confidence": "low",
            "plan_title": plan_title,
            "activity_summary": activity_summary,
            "activity_ids": [str(item) for item in selected.get("activity_ids", []) if str(item)][:3],
            "reason": "聊天可能占用了原定日程，但不能据此判断日程已完成",
        }

    def _agenda_context_for_prompt(self, *, max_entries: int = 8) -> str:
        # Prompt consumers receive a filtered future view; diagnostics remain
        # available through ``_agenda_disclosure_view('diagnostic')`` only.
        view = self._agenda_disclosure_view("future_schedule", max_entries=max_entries)
        return format_agenda_context({"entries": view.get("entries", []), "date": self._agenda_now().date().isoformat()}, max_entries=max_entries)

    def _agenda_snapshot_window(
        self,
        *,
        date_key: str,
        window: str,
        open_items: list[str] | None = None,
    ) -> dict[str, Any]:
        self._agenda_prepare_store()
        timezone_name = self._agenda_timezone_name()
        start, end = window_bounds(date_key, window, timezone_name=timezone_name)
        plans = [item for item in self._agenda_current_plan_items() if interval_overlaps_window(item, start, end, timezone_name=timezone_name)]
        activities = [item for item in self._agenda_activities_store() if interval_overlaps_window(item, start, end, timezone_name=timezone_name)]
        now = self._agenda_now()
        settled = reconcile(plans, activities, now=now)
        snapshot_id = f"agenda_snapshot:{date_key}:{window}"
        snapshot = normalize_window_snapshot(
            {
                "snapshot_id": snapshot_id,
                "date": date_key,
                "window_date": date_key,
                "window": window,
                "start_at": start.isoformat(timespec="seconds"),
                "end_at": end.isoformat(timespec="seconds"),
                "timezone": timezone_name,
                "planned": settled["plans"],
                "observed": settled["activities"],
                "reconciled": settled["reconciliations"],
                "open_items": list(open_items or []),
                "source_refs": [str(item.get("activity_id")) for item in settled["activities"] if item.get("activity_id")],
                "subject_actor_id": self._agenda_disclosure_policy.bot_id,
                "actor_type": "bot",
                "certainty": "high" if settled["reconciliations"] else "medium",
            },
            now=now,
        )
        snapshots = self._agenda_snapshots_store()
        existing = next((item for item in snapshots if item.get("snapshot_id") == snapshot_id), None)
        if existing is None:
            snapshots.append(snapshot)
        else:
            comparable_old = {key: value for key, value in existing.items() if key not in {"generated_at", "version"}}
            comparable_new = {key: value for key, value in snapshot.items() if key not in {"generated_at", "version"}}
            if comparable_old != comparable_new:
                snapshot["version"] = int(existing.get("version") or 1) + 1
                existing.clear()
                existing.update(snapshot)
            else:
                snapshot = deepcopy(existing)
        snapshots[:] = snapshots[-240:]

        reconciliation = normalize_reconciliation(
            {
                "reconciliation_id": f"reconciliation:{date_key}:{window}",
                "date": date_key,
                "window_date": date_key,
                "window": window,
                "start_at": snapshot.get("start_at"),
                "end_at": snapshot.get("end_at"),
                "timezone": timezone_name,
                "plans": settled["reconciliations"],
                "observed_activity_ids": list(snapshot.get("source_refs") or []),
                "source_refs": [snapshot_id],
                "status": "reconciled",
                "subject_actor_id": self._agenda_disclosure_policy.bot_id,
                "actor_type": "bot",
            },
            now=now,
        )
        history = self._agenda_reconciliation_store()
        old_record = next((item for item in history if item.get("reconciliation_id") == reconciliation["reconciliation_id"]), None)
        if old_record is None:
            history.append(reconciliation)
        else:
            old_record.update(reconciliation)
            reconciliation = deepcopy(old_record)
        history[:] = history[-480:]
        return snapshot

    def _agenda_closed_windows(self, now: datetime) -> list[tuple[str, str]]:
        self._agenda_prepare_store()
        timezone_name = self._agenda_timezone_name()
        local_now = now
        if local_now.tzinfo is None:
            local_now = local_now.replace(tzinfo=window_bounds(local_now.date(), "morning", timezone_name=timezone_name)[0].tzinfo)
        existing = {str(item.get("snapshot_id")) for item in self._agenda_snapshots_store() if isinstance(item, dict)}
        candidates: list[tuple[str, str]] = []
        for offset in range(-3, 2):
            target = (local_now + timedelta(days=offset)).date()
            for slug, _name, _start, _end in SCHEDULE_WINDOWS:
                _window_start, window_end = window_bounds(target, slug, timezone_name=timezone_name)
                snapshot_id = f"agenda_snapshot:{target.isoformat()}:{slug}"
                if window_end <= local_now and snapshot_id not in existing:
                    candidates.append((target.isoformat(), slug))
        return candidates

    def _agenda_maintenance_tick(self) -> list[dict[str, Any]]:
        self._agenda_prepare_store()
        settled: list[dict[str, Any]] = []
        now = self._agenda_now()
        for date_key, window in self._agenda_closed_windows(now):
            settled.append(self._agenda_snapshot_window(date_key=date_key, window=window))
        return settled
