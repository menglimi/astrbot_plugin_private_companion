"""Window-local contact allowances and projections of actual model usage."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import normalize_utc
from companion_v2.model_gateway.models import RELEASE_RECEIPT_PREFIX


DIAGNOSTIC_TASK_PREFIX = "contact-diagnostic-task-"
_ACTIVE_TASK_STATES = "'created', 'admitted', 'running', 'retry_scheduled', 'cancelling'"
_COUNTED_DECISION_CALL_STATES = "'dispatched', 'generated', 'unknown', 'discarded'"


def local_day_start(at, timezone_name):
    zone = ZoneInfo(timezone_name)
    local = normalize_utc(at, field="contact budget time").astimezone(zone)
    return datetime.combine(local.date(), time.min, tzinfo=zone).astimezone(timezone.utc)


class ContactBudget:
    def __init__(self, runtime):
        self.runtime = runtime

    def _decision_usage(self, context, preferences, at, *, exclude_task_id=""):
        timezone_name = preferences.values["quiet_timezone"]
        local = normalize_utc(at, field="contact budget time").astimezone(ZoneInfo(timezone_name))
        cutoff = stamp(local_day_start(at, timezone_name))
        task_filter = "t.task_id != ? AND t.task_id NOT LIKE ? AND "
        task_values = (exclude_task_id, DIAGNOSTIC_TASK_PREFIX + "%")

        def read(where, values):
            call_query = f"""SELECT
                    COUNT(CASE WHEN c.state IN ({_COUNTED_DECISION_CALL_STATES})
                        AND COALESCE(c.started_at, c.finished_at, r.created_at) >= ? THEN 1 END) AS calls,
                    COUNT(CASE WHEN c.state = 'reserved' THEN 1 END) AS reserved_calls
                FROM model_calls c
                JOIN model_requests r ON r.task_id = c.task_id
                JOIN execution_tasks t ON t.task_id = c.task_id
                WHERE t.kind = 'proactive.compose'
                    AND {task_filter}{where}"""
            row = self.runtime.read_one(call_query, (cutoff, *task_values, *values))
            pending_query = f"""SELECT COUNT(*) FROM execution_tasks t
                WHERE t.kind = 'proactive.compose' AND t.state IN ({_ACTIVE_TASK_STATES})
                    AND {task_filter}{where}
                    AND NOT EXISTS (SELECT 1 FROM model_calls c WHERE c.task_id = t.task_id
                        AND c.state IN ('reserved', 'dispatched', 'generated', 'unknown', 'discarded'))"""
            pending_tasks = self.runtime.read_one(pending_query, (*task_values, *values))[0]
            calls = row["calls"]
            pending = row["reserved_calls"] + pending_tasks
            return {"used": calls + pending, "calls": calls, "pending": pending,
                    "date": local.date().isoformat(), "timezone": timezone_name}

        return read("t.origin_key = ?", (context.key,))

    def attempt_usage(self, context, preferences, at):
        """Report decision calls and pending work; this count is informational only."""
        return {"session": self._decision_usage(context, preferences, at)}

    def delivery_usage(self, context, preferences, at):
        """Separate today's delivered contacts from unresolved delivery reservations."""
        timezone_name = preferences.values["quiet_timezone"]
        cutoff = stamp(local_day_start(at, timezone_name))
        base = """SELECT
                COUNT(DISTINCT CASE WHEN d.state IN ('pending', 'dispatched', 'accepted', 'unknown')
                    OR (d.state = 'delivered' AND d.delivered_at >= ?) THEN c.contact_id END) AS occupied,
                COUNT(DISTINCT CASE WHEN d.state = 'delivered' AND d.delivered_at >= ?
                    THEN c.contact_id END) AS delivered,
                COUNT(DISTINCT CASE WHEN d.state IN ('pending', 'dispatched', 'accepted', 'unknown')
                    THEN c.contact_id END) AS unresolved
            FROM proactive_contacts c
            JOIN background_deliveries d ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            JOIN execution_tasks t ON t.task_id = c.task_id
            WHERE d.owner = 'proactive.contact'
                AND COALESCE(json_extract(c.record_json, '$.administrator_test'), 0) = 0
                AND """

        def read(where, params, limit):
            row = self.runtime.read_one(base + where, (cutoff, cutoff, *params))
            return {"used": row["occupied"], "limit": limit, "actual": row["delivered"],
                    "delivered": row["delivered"],
                    "reserved": row["unresolved"], "occupied": row["occupied"],
                    "unresolved": row["unresolved"], "date": normalize_utc(at, field="contact budget time").astimezone(
                        ZoneInfo(timezone_name)).date().isoformat(), "timezone": timezone_name}

        return {"session": read("c.origin_key = ?", (context.key,), preferences.values["max_contacts_24h"])}

    def rhythm_snapshot(self, context, preferences, at):
        """Summarize confirmed contact timing for the generation prompt, without message content."""
        timezone_name = preferences.values["quiet_timezone"]
        local_at = normalize_utc(at, field="contact rhythm time").astimezone(ZoneInfo(timezone_name))
        now = stamp(at)
        day_start = stamp(local_day_start(at, timezone_name))
        recent_start = stamp(normalize_utc(at, field="contact rhythm time") - timedelta(hours=4))
        row = self.runtime.read_one("""SELECT
                COUNT(DISTINCT CASE WHEN d.delivered_at >= ? THEN c.contact_id END) AS delivered_today,
                COUNT(DISTINCT CASE WHEN d.delivered_at >= ? THEN c.contact_id END) AS delivered_recently
            FROM proactive_contacts c
            JOIN background_deliveries d ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            WHERE c.origin_key = ? AND d.owner = 'proactive.contact' AND d.state = 'delivered'
                AND d.delivered_at <= ?
                AND COALESCE(json_extract(c.record_json, '$.administrator_test'), 0) = 0
                AND d.delivered_at >= ?""",
            (day_start, recent_start, context.key, now, min(day_start, recent_start)))
        latest = self.runtime.read_one("""SELECT d.delivered_at, json_extract(c.record_json, '$.purpose') AS purpose
            FROM proactive_contacts c
            JOIN background_deliveries d ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            WHERE c.origin_key = ? AND d.owner = 'proactive.contact' AND d.state = 'delivered'
                AND d.delivered_at <= ?
                AND COALESCE(json_extract(c.record_json, '$.administrator_test'), 0) = 0
            ORDER BY d.delivered_at DESC LIMIT 1""", (context.key, now))
        elapsed = None
        last_purpose = None
        if latest is not None:
            elapsed = max(0, int((normalize_utc(at, field="contact rhythm time")
                                  - datetime.fromisoformat(latest[0]).astimezone(timezone.utc)
                                  ).total_seconds() // 60))
            if isinstance(latest["purpose"], str) and latest["purpose"]:
                last_purpose = latest["purpose"]
        recent_purposes_rows = self.runtime.read_all("""SELECT json_extract(c.record_json, '$.purpose') AS purpose
            FROM proactive_contacts c
            JOIN background_deliveries d ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            WHERE c.origin_key = ? AND d.owner = 'proactive.contact' AND d.state = 'delivered'
                AND d.delivered_at >= ? AND d.delivered_at <= ?
                AND COALESCE(json_extract(c.record_json, '$.administrator_test'), 0) = 0
            ORDER BY d.delivered_at DESC LIMIT 6""", (context.key, day_start, now))
        recent_purposes = []
        for item in recent_purposes_rows:
            purpose = item["purpose"]
            if isinstance(purpose, str) and purpose and purpose not in recent_purposes:
                recent_purposes.append(purpose)
        snapshot = {"local_time": local_at.strftime("%H:%M"),
                    "delivered_today": row["delivered_today"],
                    "daily_maximum": preferences.values["max_contacts_24h"],
                    "delivered_last_4_hours": row["delivered_recently"],
                    "minutes_since_last_delivery": elapsed}
        if last_purpose is not None:
            snapshot["last_delivered_purpose"] = last_purpose
        if recent_purposes:
            snapshot["recent_purposes_today"] = recent_purposes
        return snapshot

    def model_usage(self, context, preferences, at):
        """Provider usage for this local day; local Laya decisions have no monetary ledger."""
        timezone_name = preferences.values["quiet_timezone"]
        cutoff = stamp(local_day_start(at, timezone_name))
        base = """SELECT
                COUNT(CASE WHEN c.state IN ('dispatched', 'generated', 'unknown', 'discarded') THEN 1 END) AS calls,
                COUNT(CASE WHEN c.state = 'reserved' THEN 1 END) AS pending_reservations,
                COUNT(CASE WHEN c.state = 'unknown' THEN 1 END) AS unknown_calls,
                COUNT(CASE WHEN c.actual_tokens IS NOT NULL THEN 1 END) AS confirmed_calls,
                SUM(COALESCE(c.actual_tokens, 0)) AS tokens,
                SUM(COALESCE(c.actual_cost, 0)) AS cost_microusd,
                COUNT(c.actual_cost) AS cost_samples,
                SUM(CASE WHEN c.actual_tokens IS NULL AND c.state IN ('reserved', 'dispatched', 'unknown')
                         AND COALESCE(c.receipt_ref, '') NOT LIKE ?
                         THEN c.reserved_tokens ELSE 0 END) AS held_tokens,
                SUM(CASE WHEN c.actual_cost IS NULL AND c.state IN ('reserved', 'dispatched', 'unknown')
                         AND COALESCE(c.receipt_ref, '') NOT LIKE ?
                         THEN c.reserved_cost ELSE 0 END) AS held_cost_microusd,
                AVG(c.latency_ms) AS avg_latency_ms
            FROM model_calls c
            JOIN model_requests r ON r.task_id = c.task_id
            JOIN execution_tasks t ON t.task_id = c.task_id
            WHERE t.kind IN ('proactive.compose', 'proactive.judge')
                AND COALESCE(c.started_at, c.finished_at, r.created_at) >= ? AND """

        row = self.runtime.read_one(base + "t.origin_key = ?",
            (RELEASE_RECEIPT_PREFIX + "%", RELEASE_RECEIPT_PREFIX + "%", cutoff, context.key))
        return {"session": {"calls": row["calls"], "pending_reservations": row["pending_reservations"],
                    "unknown_calls": row["unknown_calls"], "confirmed_calls": row["confirmed_calls"],
                    "tokens": row["tokens"],
                    "cost_microusd": row["cost_microusd"] if row["cost_samples"] else None,
                    "held_tokens": row["held_tokens"], "held_cost_microusd": row["held_cost_microusd"],
                    "avg_latency_ms": round(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None,
                    "date": normalize_utc(at, field="contact budget time").astimezone(ZoneInfo(timezone_name)).date().isoformat(),
                    "timezone": timezone_name}}

    def usage_summary(self, context, preferences, at):
        """Report model decisions, real model usage, and delivery quota independently."""
        timezone_name = preferences.values["quiet_timezone"]
        return {"period": "local_day",
                "date": normalize_utc(at, field="contact budget time").astimezone(ZoneInfo(timezone_name)).date().isoformat(),
                "timezone": timezone_name,
                "decisions": self.attempt_usage(context, preferences, at),
                "deliveries": self.delivery_usage(context, preferences, at),
                "model": self.model_usage(context, preferences, at)}

    def contacts(self, context, preferences, at, *, exclude_contact_id=""):
        # Pending drafts reserve a place. An unresolved wire effect continues to
        # reserve it until a trusted receipt resolves what actually happened.
        cutoff = stamp(local_day_start(at, preferences.values["quiet_timezone"]))
        count = self.runtime.read_one("""SELECT COUNT(DISTINCT c.contact_id) FROM proactive_contacts c
            JOIN background_deliveries d ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            WHERE c.origin_key = ? AND d.owner = 'proactive.contact' AND c.contact_id != ?
            AND COALESCE(json_extract(c.record_json, '$.administrator_test'), 0) = 0
            AND (d.state IN ('pending', 'dispatched', 'accepted', 'unknown')
                OR (d.state = 'delivered' AND d.delivered_at >= ?))""",
            (context.key, exclude_contact_id, cutoff))[0]
        if count >= preferences.values["max_contacts_24h"]:
            raise TaskError("contact_delivery_budget", "contact delivery allowance is exhausted")
