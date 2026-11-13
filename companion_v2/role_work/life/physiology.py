"""Role-level sleep pressure, recovery, workload and bounded sleep quality."""

from datetime import datetime

from companion_v2.execution.models import digest
from companion_v2.foundation.refs import normalize_utc
from companion_v2.role_work.models import RoleActivityState as State, RoleSleepReceipt


def actual_sleep_seconds(activity, now):
    receipt = activity.sleep
    recorded = receipt.slept_seconds if receipt else 0
    if activity.state is State.ACTIVE:
        now = normalize_utc(now, field="sleep observation")
        return min(172800, recorded + max(0, round((now - activity.updated_at).total_seconds())))
    if receipt:
        return recorded
    if activity.state in (State.COMPLETED, State.CANCELLED):
        return max(0, round((activity.updated_at - activity.started_at).total_seconds()))
    return 0


def _legacy_base_need(activity):
    if activity.work and activity.work.due_at:
        minutes = round((activity.work.due_at - activity.started_at).total_seconds() / 60)
        return max(1, min(1440, minutes))
    return 480


def sleep_profile(episodes, *, workload_score, now):
    """Calculate current role physiology from durable sleep episodes and observations."""
    now = normalize_utc(now, field="sleep observation")
    episodes = tuple(sorted(episodes, key=lambda item: (item.started_at, item.activity_id)))
    debt = 0.0
    for activity in episodes:
        if activity.state in (State.ACTIVE, State.PAUSED, State.PLANNED):
            continue
        receipt = activity.sleep
        kind = receipt.kind if receipt else "night"
        base = receipt.base_need_minutes if receipt else _legacy_base_need(activity)
        if kind == "manual" and receipt and receipt.target_minutes <= 60:
            kind = "nap"
        slept = actual_sleep_seconds(activity, now) / 60
        quality = ((receipt.quality_min + receipt.quality_max) / 200) if receipt else 0.78
        if kind == "nap":
            debt = max(0.0, debt - slept * quality * 0.12)
        else:
            debt += max(0.0, base - slept)
            debt = max(0.0, debt - min(debt, max(0.0, slept - base) * quality * 0.75))
        debt = min(720.0, debt)

    workload_score = max(0, min(100, int(workload_score)))
    latest = episodes[-1] if episodes else None
    if latest is None:
        awake_minutes = None
        pressure = None
    elif latest.state is State.ACTIVE:
        awake_minutes = 0
        pressure = 0
    else:
        awake_minutes = max(0, round((now - latest.updated_at).total_seconds() / 60))
        pressure = min(100, round(awake_minutes / 60 * 4.0 + debt * 0.14 + workload_score * 0.12))
    return {"sleep_pressure": pressure, "sleep_debt_minutes": round(debt),
            "workload_score": workload_score, "awake_minutes": awake_minutes,
            "has_sleep_history": latest is not None}


def sleep_quality_range(workload_score, sleep_debt_minutes, interruptions=0, early_interruptions=0):
    workload = max(0, min(100, int(workload_score)))
    debt = max(0, min(720, int(sleep_debt_minutes)))
    interruptions = max(0, min(20, int(interruptions)))
    early_interruptions = max(0, min(interruptions, int(early_interruptions)))
    penalty = workload * 0.18 + min(20, debt * 0.025) + interruptions * 7 + early_interruptions * 5
    lower = max(25, min(92, round(82 - penalty)))
    upper = max(lower + 3, min(98, round(95 - workload * 0.10 - min(12, debt * 0.015)
                                     - interruptions * 4 - early_interruptions * 3)))
    return lower, upper


def sleep_receipt(*, kind, base_need_minutes, target_minutes, workload_score, sleep_debt_minutes):
    quality_min, quality_max = sleep_quality_range(workload_score, sleep_debt_minutes)
    return RoleSleepReceipt(kind=kind, base_need_minutes=base_need_minutes,
        target_minutes=target_minutes, workload_score=workload_score,
        quality_min=quality_min, quality_max=quality_max)


def recommended_sleep_minutes(base_need_minutes, sleep_debt_minutes, workload_score):
    base = max(1, min(1440, int(base_need_minutes)))
    recovery = min(120, round(max(0, sleep_debt_minutes) * 0.45))
    workload = 30 if workload_score >= 80 else 15 if workload_score >= 55 else 0
    return min(1440, base + recovery + workload)


def night_sleep_ready(profile):
    pressure = profile.get("sleep_pressure")
    if pressure is None:
        return True  # With no receipt history there is no pressure estimate to delay the first night.
    threshold = max(48, 66 - profile.get("workload_score", 0) * 0.12
                    - profile.get("sleep_debt_minutes", 0) * 0.035)
    return pressure >= threshold


def nap_ready(profile, *, local_minutes):
    pressure = profile.get("sleep_pressure")
    return (profile.get("has_sleep_history") is True and profile.get("awake_minutes") is not None
            and profile["awake_minutes"] >= 300 and 660 <= local_minutes < 960
            and pressure is not None and pressure >= 40
            and (profile.get("workload_score", 0) >= 40 or profile.get("sleep_debt_minutes", 0) >= 90))


def oversleep_extension(activity, *, sleep_debt_minutes, roll=None):
    """A single, repeatable snooze extension; high debt/workload makes it likelier."""
    receipt = activity.sleep
    if (receipt is None or receipt.oversleep_minutes or receipt.kind == "nap"
            or receipt.kind == "manual" and receipt.target_minutes <= 60):
        return 0
    chance = min(70, 8 + min(30, sleep_debt_minutes // 10)
                 + receipt.workload_score // 4 + receipt.interruptions * 8)
    if roll is None:
        roll = int(digest([activity.activity_id, "oversleep"])[:8], 16) % 100
    if roll >= chance:
        return 0
    return 30 if chance >= 45 else 15
