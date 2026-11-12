"""Goal-driven background research for upcoming events and lasting interests.

The old V1 exploration could decide on its own what to search.  V2's search
source only repeated one configured interest string.  This module restores the
useful part without turning the companion into an unbounded browser:

* a goal is discovered from an upcoming date or a configured interest;
* at most one goal is researched per session;
* queries are generated from that goal and validated to contain a concrete
  personal signal (an interest or the event label);
* results are scored and stored as bounded research notes;
* nothing here sends a message.  Delivery remains the proactive pipeline's job,
  and surprise-sensitive goals are explicitly ``private_note``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re

from companion_v2.execution.models import digest, stamp
from companion_v2.foundation.refs import ScopeKind

from .external_materials import ParseBatch, canonical_news_url


KEY_PREFIX = "proactive.exploration:"
LOOKAHEAD_DAYS = 21
MAX_QUERIES = 3
RESULTS_PER_QUERY = 4
MAX_NOTES = 12
MAX_DAILY_SESSIONS = 3
GOAL_COOLDOWN = timedelta(hours=24)
PRIVATE_LABELS = ("生日", "纪念日", "周年")
GIFT_TERMS = ("礼物", "推荐", "实用", "体验", "入门", "攻略", "注意")
_PRIVATE_QUERY = re.compile(r"(\+?\d[\d -]{7,}\d|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})")


def _interests(value):
    if type(value) is not str:
        return ()
    parts = re.split(r"[,，、;；\n]+", value)
    result = []
    for part in parts:
        text = " ".join(part.split()).strip(" .。")
        if 2 <= len(text) <= 40 and text not in result:
            result.append(text)
    return tuple(result[:8])


def _safe_query(value):
    text = " ".join(value.split()).strip()
    if not text or len(text) > 120 or _PRIVATE_QUERY.search(text):
        return ""
    return text


def plan_goals(entries, interests, *, lookahead_days=LOOKAHEAD_DAYS):
    """Turn near dates and interests into ranked research goals.

    ``entries`` are the read-only rows returned by ``DateContactEvents.upcoming``.
    The function is deliberately pure so query selection can be tested without a
    host, a model or a search provider.
    """
    goals = []
    interests = tuple(interests)
    for entry in entries or ():
        if type(entry) is not dict:
            continue
        days = entry.get("days_until")
        if type(days) is not int or not 0 <= days <= lookahead_days:
            continue
        basis = str(entry.get("basis") or "")
        label = " ".join(str(entry.get("label") or "").split())[:80]
        if not label:
            continue
        # Fixed holidays and the character's own birthday are not preparation
        # goals for the private partner: they would produce generic shopping
        # queries with no one to give the result to.
        if basis == "fixed_gregorian_holiday" or basis == "profile_role_birthday":
            continue
        if "生日" in label and basis != "profile_role_birthday":
            kind = "birthday_gift"
            share_policy = "private_note"
            priority = 40 + max(0, lookahead_days - days)
        elif basis in ("profile_relationship_date", "auto_relationship_anniversary") or "纪念" in label:
            kind = "anniversary_preparation"
            share_policy = "private_note"
            priority = 40 + max(0, lookahead_days - days)
        else:
            kind = "event_preparation"
            share_policy = "can_ask"
            priority = 20 + max(0, lookahead_days - days)
        goal_id = "explore:" + digest([kind, label, entry.get("date") or days])
        goals.append({
            "goal_id": goal_id,
            "kind": kind,
            "label": label,
            "date": entry.get("date"),
            "days_until": days,
            "basis": basis,
            "interests": interests,
            "share_policy": share_policy,
            "priority": priority,
        })
    for interest in interests:
        goal_id = "explore:" + digest(["interest_tracking", interest])
        goals.append({
            "goal_id": goal_id,
            "kind": "interest_tracking",
            "label": interest,
            "date": None,
            "days_until": None,
            "basis": "interest",
            "interests": (interest,),
            "share_policy": "can_share",
            "priority": 5,
        })
    unique, seen = [], set()
    for goal in sorted(goals, key=lambda item: (-item["priority"], item["goal_id"])):
        if goal["goal_id"] in seen:
            continue
        seen.add(goal["goal_id"])
        unique.append(goal)
    return tuple(unique)


def build_queries(goal):
    """Build 1-3 concrete queries for one goal.

    Every query carries either an interest or the event label.  A bare
    ``生日礼物`` query is exactly what made the old feature return shopping
    listicles, so it is never emitted when an interest is available.
    """
    kind = goal.get("kind")
    interests = tuple(goal.get("interests") or ())
    label = str(goal.get("label") or "").strip()
    queries = []

    def add(value):
        text = _safe_query(value)
        if text and text not in queries:
            queries.append(text)

    if kind == "birthday_gift":
        first = interests[0] if interests else ""
        second = interests[1] if len(interests) > 1 else first
        if first:
            add(f"{first} 生日礼物 平价 实用")
            add(f"{first} 生日礼物 中档 质感")
            add(f"{second or first} 体验 生日 礼物")
        else:
            add("生日礼物 实用 推荐")
    elif kind == "anniversary_preparation":
        first = interests[0] if interests else ""
        if first:
            add(f"{first} 纪念日 礼物 用心")
            add(f"{first} 纪念日 体验 安排")
        else:
            add("纪念日 礼物 推荐")
    elif kind == "event_preparation":
        first = interests[0] if interests else ""
        add(f"{label} {first} 准备 攻略".strip())
        add(f"{label} 注意事项 准备".strip())
    else:
        first = interests[0] if interests else label
        add(f"{first} 最近 新消息")
        add(f"{first} 入门 知识")
    return tuple(queries[:MAX_QUERIES])


def _item_text(item):
    title = getattr(item, "title", "") or ""
    summary = getattr(item, "summary", "") or ""
    return f"{title} {summary}".casefold()


def score_item(item, goal, *, at=None):
    """Heuristic first pass; the planner never spends a model call on noise."""
    text = _item_text(item)
    if not text.strip():
        return 0.0
    interests = tuple(goal.get("interests") or ())
    matched = sum(1 for interest in interests if interest.casefold() in text)
    interest_score = matched / max(1, len(interests))
    term_score = sum(1 for term in GIFT_TERMS if term in text) / len(GIFT_TERMS)
    label_score = 1.0 if str(goal.get("label") or "").casefold() in text else 0.0
    source_score = 1.0 if getattr(item, "url", "") else 0.0
    published = getattr(item, "published_at", None)
    freshness = 0.5
    if isinstance(published, datetime) and published.tzinfo is not None:
        now = at if isinstance(at, datetime) and at.tzinfo is not None else datetime.now(timezone.utc)
        age = now - published.astimezone(timezone.utc)
        if age <= timedelta(days=30):
            freshness = 1.0
        elif age <= timedelta(days=180):
            freshness = 0.6
        else:
            freshness = 0.2
    return round(0.45 * interest_score + 0.20 * term_score + 0.15 * label_score
                 + 0.10 * source_score + 0.10 * freshness, 3)


def _note(item, goal, score, at):
    published = getattr(item, "published_at", None)
    try:
        url = canonical_news_url(getattr(item, "url", ""))
    except (TypeError, ValueError):
        url = ""
    title = " ".join(str(getattr(item, "title", "") or "").split())[:240]
    summary = " ".join(str(getattr(item, "summary", "") or "").split())[:500]
    source = " ".join(str(getattr(item, "source", "") or "").split())[:100]
    if not title or not url or not source:
        return None
    return {
        "note_id": "explore-note:" + digest([goal["goal_id"], url]),
        "goal_id": goal["goal_id"],
        "kind": goal["kind"],
        "label": goal["label"],
        "title": title,
        "summary": summary,
        "url": url,
        "source": source,
        "published_at": stamp(published) if isinstance(published, datetime) else "",
        "score": score,
        "share_policy": goal.get("share_policy", "private_note"),
        "created_at": stamp(at),
    }


class ExplorationEvents:
    """Background goal research; notes only, never a direct send."""

    def __init__(self, contacts, date_events, port):
        self.contacts, self.runtime = contacts, contacts.runtime
        self.date_events, self.port = date_events, port
        self._workers = {}
        self._closed = False

    def _key(self, context):
        return KEY_PREFIX + digest(context.key)

    def _load(self, context):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (self._key(context),))
        if row is None:
            return {}
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError, KeyError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, context, value):
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (self._key(context), payload))

    def read(self, context):
        """Public, bounded snapshot used by the dashboard/command surface."""
        state = self._load(context)
        return {
            "last_run_at": state.get("last_run_at", ""),
            "last_result": state.get("last_result", ""),
            "last_goal": state.get("last_goal", {}),
            "notes": list(state.get("notes", ()))[:MAX_NOTES],
        }

    def close(self):
        self._closed = True
        for worker in tuple(self._workers.values()):
            worker.cancel()
        self._workers.clear()

    def _due(self, context, values, at):
        if not values.get("web_exploration_enabled") is True:
            return False
        if values.get("news_source_kind") != "search":
            return False
        if context.scope.kind is not ScopeKind.PRIVATE:
            return False
        state = self._load(context)
        last = state.get("last_run_at")
        if last:
            try:
                previous = datetime.fromisoformat(last)
            except (TypeError, ValueError):
                previous = None
            interval = max(1, int(values.get("web_exploration_min_interval_hours") or 8))
            if previous is not None and at - previous < timedelta(hours=interval):
                return False
        today = at.astimezone(timezone.utc).date().isoformat()
        sessions = state.get("sessions", ())
        if isinstance(sessions, list) and sum(1 for item in sessions if item == today) >= MAX_DAILY_SESSIONS:
            return False
        return True

    def maintain(self, context, preferences):
        """Start at most one bounded research worker for this window."""
        if self._closed or self.port is None:
            return
        values = dict(preferences.values)
        at = self.contacts.tasks.now()
        if not self._due(context, values, at):
            return
        worker = self._workers.get(context.key)
        if worker is not None:
            if not worker.done():
                return
            if not worker.cancelled():
                worker.exception()
            self._workers.pop(context.key, None)
        self._workers[context.key] = asyncio.create_task(
            self._run(context, values), name="companion:exploration:" + digest(context.key))

    async def _run(self, context, values):
        at = self.contacts.tasks.now()
        state = self._load(context)
        state["sessions"] = [*state.get("sessions", ()), at.astimezone(timezone.utc).date().isoformat()][-30:]
        try:
            if self.date_events is None:
                state.update(last_run_at=stamp(at), last_result="no_date_source")
                self._save(context, state)
                return
            entries = self.date_events.upcoming(
                context, type("ExplorationPreferences", (), {"values": values})(),
                at, lookahead_days=LOOKAHEAD_DAYS)
            interests = _interests(values.get("web_exploration_interests"))
            goals = plan_goals(entries, interests)
            last_goals = state.get("last_goal_at", {})
            if not isinstance(last_goals, dict):
                last_goals = {}
            eligible = []
            for goal in goals:
                previous = last_goals.get(goal["goal_id"])
                if previous:
                    try:
                        if at - datetime.fromisoformat(previous) < GOAL_COOLDOWN:
                            continue
                    except (TypeError, ValueError):
                        pass
                eligible.append(goal)
            if not eligible:
                state.update(last_run_at=stamp(at), last_result="no_goal")
                self._save(context, state)
                return
            goal = eligible[0]
            queries = build_queries(goal)
            if not queries:
                state.update(last_run_at=stamp(at), last_result="no_query")
                self._save(context, state)
                return
            items = await self._search(context, values, queries)
            scored = []
            seen = set()
            for item in items:
                try:
                    url = canonical_news_url(getattr(item, "url", ""))
                except (TypeError, ValueError):
                    url = ""
                if not url or url in seen:
                    continue
                seen.add(url)
                score = score_item(item, goal, at=at)
                if score < 0.35:
                    continue
                note = _note(item, goal, score, at)
                if note is not None:
                    scored.append(note)
            scored.sort(key=lambda note: note["score"], reverse=True)
            notes = [*scored[:3], *state.get("notes", ())]
            # Keep the newest note for a URL; old snapshots remain bounded.
            unique, seen_notes = [], set()
            for note in notes:
                if note["note_id"] in seen_notes:
                    continue
                seen_notes.add(note["note_id"])
                unique.append(note)
            state["notes"] = unique[:MAX_NOTES]
            last_goals[goal["goal_id"]] = stamp(at)
            state["last_goal_at"] = dict(sorted(
                last_goals.items(), key=lambda item: item[1], reverse=True)[:64])
            state["last_goal"] = {key: goal.get(key) for key in
                                  ("goal_id", "kind", "label", "days_until", "share_policy")}
            state["last_run_at"] = stamp(at)
            state["last_result"] = "notes" if scored else "no_result"
            self._save(context, state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state.update(last_run_at=stamp(at), last_result="failed")
            state["last_error"] = type(error).__name__[:80]
            self._save(context, state)

    async def _search(self, context, values, queries):
        resolver = getattr(self.port, "for_settings", None)
        if not callable(resolver):
            return []
        items = []
        for query in queries:
            if self._closed:
                break
            current = dict(values)
            current["web_exploration_interests"] = query
            current["web_exploration_result_count"] = RESULTS_PER_QUERY
            try:
                port = self.port.for_settings(current, context)
                if port is None:
                    continue
                batch = await asyncio.wait_for(port.fetch(), timeout=12)
                if not isinstance(batch, ParseBatch):
                    continue
                items.extend(batch.items[:RESULTS_PER_QUERY])
            except asyncio.CancelledError:
                raise
            except Exception:
                # One failed query must not discard the other two.
                continue
        return items
