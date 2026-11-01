"""Read-only projection of current, governed sources for proactive contact."""

from datetime import datetime, timedelta, timezone
import json

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind

from .configuration import GROUP as CONTACT_OWNER, PURPOSES
from .balance_events import SOURCE_OWNER as BALANCE_OWNER
from .calendar_events import SOURCE_OWNER as CALENDAR_OWNER
from .events import SOURCE_OWNER as ACTIVITY_OWNER
from .growth_events import SOURCE_OWNER as GROWTH_OWNER
from .journal_events import JOURNAL_SOURCE_OWNER
from .news_events import SOURCE_OWNER as NEWS_OWNER
from .official_alert_events import SOURCE_OWNER as ALERT_OWNER
from .reminiscence_events import SOURCE_OWNER as REMINISCENCE_OWNER
from .screen_events import SOURCE_OWNER as SCREEN_OWNER
from .weather_change_events import SOURCE_OWNER as WEATHER_CHANGE_OWNER
from .weather_events import SOURCE_OWNER as WEATHER_OWNER


SOURCE_CATEGORIES = {
    ACTIVITY_OWNER: ("生活记录", "生活分享"),
    GROWTH_OWNER: ("生活记录", "成长与突破"),
    JOURNAL_SOURCE_OWNER: ("生活记录", "生活分享"),
    CALENDAR_OWNER: ("约定", "约定与提醒"),
    REMINISCENCE_OWNER: ("共同记忆", "旧忆回响"),
    WEATHER_OWNER: ("环境与状态", "环境与状态变化"),
    WEATHER_CHANGE_OWNER: ("环境与状态", "环境与状态变化"),
    ALERT_OWNER: ("环境与状态", "环境与状态变化"),
    BALANCE_OWNER: ("环境与状态", "环境与状态变化"),
    NEWS_OWNER: ("资讯", "外部资讯"),
    SCREEN_OWNER: ("屏幕观察", "已授权屏幕观察"),
}
SOURCE_LABELS = {
    ACTIVITY_OWNER: "角色活动回执",
    GROWTH_OWNER: "角色成长回执",
    JOURNAL_SOURCE_OWNER: "角色日记",
    CALENDAR_OWNER: "已接受约定",
    REMINISCENCE_OWNER: "共同关系记忆",
    WEATHER_OWNER: "天气观测",
    WEATHER_CHANGE_OWNER: "天气变化",
    ALERT_OWNER: "官方气象预警",
    BALANCE_OWNER: "服务余额",
    NEWS_OWNER: "资讯源",
    SCREEN_OWNER: "授权屏幕回执",
}
# Stored alerts carry the CAP severity enum, but the material page is for
# administrators. Reuse the wording the management UI already shows for the
# same levels (pages/陪伴小屋/state-contact.js) instead of leaking the English
# enum into the note.
ALERT_SEVERITY_LABELS = {"minor": "一般", "moderate": "较重", "severe": "严重",
                         "extreme": "特别严重", "unknown": "未知"}
THOUGHT_ORIGINS = {
    "relationship_event": "关系话题",
    "reminiscence_event": "旧忆回响",
    "followup_event": "旧话题接续",
    "burst_event": "近期对话",
    "greeting_event": "问候时机",
    "activity_event": "角色生活片段",
    "growth_event": "角色成长进展",
    "journal_event": "角色日记",
    "calendar_event": "已接受的约定",
    "date_event": "日期节点",
    "meal_event": "饭点语境",
    "screen_event": "授权屏幕观察",
    "weather_change_event": "天气变化",
    "official_alert_event": "天气预警",
    "weather_event": "当前天气",
    "balance_event": "服务余额",
    "news_event": "资讯分享",
}


def _text(value, limit=480):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _weather(value):
    if not isinstance(value, dict):
        return _text(value, 160)
    parts = [_text(value.get("condition"), 64)]
    temperature = value.get("temperature_c")
    if type(temperature) in (int, float):
        parts.append(f"{temperature:g}°C")
    humidity = value.get("humidity_pct")
    if type(humidity) in (int, float):
        parts.append(f"湿度 {humidity:g}%")
    return " · ".join(part for part in parts if part)


def _alert_severity(value):
    raw = _text(value, 40).lower()
    return ALERT_SEVERITY_LABELS.get(raw, raw)


def _record_projection(record, settings, at):
    try:
        body = json.loads(record.payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    body.pop("_receipt", None)
    owner = record.reference.owner
    category, purpose = SOURCE_CATEGORIES[owner]
    expires_at = record.reference.expires_at

    if owner == ACTIVITY_OWNER:
        if at >= record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600)):
            return None
        if body.get("event") == "reading_completed":
            title = "完成阅读"
        elif body.get("event") == "creative_artifact_completed":
            title = "完成创作 · " + (_text(body.get("activity_title"), 96) or "随笔")
        else:
            title = "角色生活片段"
        detail = _text(body.get("excerpt") or body.get("meaning"))
        note = _text(body.get("subjective_reflection"), 240)
        expires_at = record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600))
    elif owner == GROWTH_OWNER:
        if at >= record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600)):
            return None
        label = "达成目标" if body.get("event") == "goal_achieved" else (
            "技能练习" if body.get("kind") == "skill" else "目标推进")
        title = label + " · " + (_text(body.get("title"), 120) or "角色成长")
        detail = _text(body.get("progress_note") or body.get("description") or body.get("meaning"))
        note = ""
        expires_at = record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600))
    elif owner == JOURNAL_SOURCE_OWNER:
        if at >= record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600)):
            return None
        title = ("梦境记录" if body.get("kind") == "dream" else "日记记录") + " · " + _text(body.get("local_date"), 32)
        detail = _text(body.get("passage") or body.get("meaning"))
        note = "梦境记录属于角色的主观记忆。" if body.get("kind") == "dream" else ""
        expires_at = record.reference.issued_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600))
    elif owner == CALENDAR_OWNER:
        if body.get("event") == "accepted_commitment_concluded":
            title = "约定余温 · " + (_text(body.get("title"), 140) or "已结束约定")
        elif body.get("event") == "role_plan_window_concluded":
            title = "日程结束 · " + (_text(body.get("title"), 140) or "角色安排")
        else:
            title = _text(body.get("title"), 160) or "已接受的约定"
        detail = " · ".join(part for part in (_text(body.get("detail"), 220),
            _text(body.get("local_end") or body.get("local_start") or body.get("starts_at"), 64)) if part)
        note = ""
    elif owner == REMINISCENCE_OWNER:
        title = "共同情绪片段" if body.get("kind") == "emotion_episode" else "共同关系记忆"
        detail = _text(body.get("interpretation") or body.get("meaning"), 360)
        note = "过往时间 " + _text(body.get("observed_at"), 64)
    elif owner == WEATHER_OWNER:
        title = "当前天气 · " + (_text(body.get("role_location"), 64) or "角色所在地")
        detail = _weather(body)
        note = "观测时间 " + _text(body.get("observed_at"), 64)
    elif owner == WEATHER_CHANGE_OWNER:
        title = "天气变化 · " + (_text(body.get("change_kind"), 80) or "观测变化")
        detail = " → ".join(part for part in (_weather(body.get("previous")), _weather(body.get("current"))) if part)
        note = ""
    elif owner == ALERT_OWNER:
        alert = body.get("alert") if isinstance(body.get("alert"), dict) else {}
        # Stored alerts are released dataclasses (headline/event_name), not the
        # title/name keys some older payloads used; read the real fields first
        # and keep the legacy keys as a fallback so old rows still render.
        title = (_text(alert.get("headline"), 160) or _text(alert.get("event_name"), 160)
                 or _text(alert.get("title"), 160) or _text(alert.get("name"), 160) or "官方气象预警")
        severity = _alert_severity(alert.get("severity") or alert.get("level"))
        detail = _text(alert.get("description") or alert.get("text"), 360)
        note = " · ".join(part for part in (_text(body.get("alert_city"), 64), severity) if part)
    elif owner == BALANCE_OWNER:
        title = "服务余额提醒"
        detail = "极低余额" if body.get("level") == "critical" else "低余额"
        if settings.get("balance_include_amount_in_message") is True and "amount" in body:
            detail += " · " + _text(str(body.get("amount")) + str(body.get("currency") or ""), 64)
        note = ""
    elif owner == NEWS_OWNER:
        category = _text(body.get("category"), 48) or category
        title = _text(body.get("title"), 180) or "外部资讯"
        detail = _text(body.get("summary"), 360)
        note = _text(body.get("source"), 100)
    else:
        title = "已授权屏幕观察"
        detail = _text(body.get("interpretation"), 360)
        note = _text(body.get("observed_at"), 64)

    projection = {"id": digest([record.reference.source_id, record.reference.version.lineage_id,
                           record.reference.version.revision])[:20],
            "category": category, "purpose": purpose, "title": title, "detail": detail,
            "note": note, "source": SOURCE_LABELS[owner], "captured_at": record.reference.issued_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None}
    if owner == NEWS_OWNER:
        projection["url"] = body.get("url", "")
    return projection


def _latest_conversation(schedule, context, at, settings):
    if context.scope.kind is not ScopeKind.PRIVATE:
        return None
    reader = getattr(schedule, "latest_conversation", None)
    row = (reader(context) if callable(reader) else schedule.runtime.read_one("""SELECT f.state AS flow_state, b.batch_id
        FROM conversation_flows f JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
        ORDER BY f.sequence DESC LIMIT 1""", (context.scope.key,)))
    if row is None:
        return None
    try:
        _, _, event = schedule._origin(context, row["batch_id"], at)
        if any(getattr(event, name, ()) for name in ("images", "audios", "videos", "forwards", "quotes")):
            return None
        source = schedule.messages._read_source(event.source, context, event.capability, at)
        if source.content_hash != event.input_hash or digest(source.payload) != source.content_hash:
            return None
        expires_at = event.received_at + timedelta(seconds=settings.get("event_max_age_seconds", 3600))
        if at >= expires_at:
            return None
        return {"id": digest([event.scoped_key, event.input_hash])[:20], "category": "对话",
                "purpose": "日常承接、关系表达", "title": "最近私聊内容",
                "detail": _text(source.payload), "note": "",
                "source": "当前私聊", "captured_at": event.received_at.isoformat(),
                "expires_at": expires_at.isoformat()}
    except (TaskError, KeyError, TypeError, ValueError, UnicodeError, OSError, AttributeError):
        return None


def _response_observation(interaction, delivered_at, observed_at):
    observation = {"status": "unknown", "observed_through": observed_at.isoformat()}
    if type(interaction) is not dict or not isinstance(delivered_at, str):
        return observation
    try:
        delivered = datetime.fromisoformat(delivered_at)
        observed = observed_at
        if delivered.tzinfo is None or observed.tzinfo is None:
            return observation
        delivered = delivered.astimezone(timezone.utc)
        observed = observed.astimezone(timezone.utc)
        if delivered > observed:
            return observation
    except (TypeError, ValueError, OverflowError, OSError):
        return observation
    if "last_input_at" not in interaction:
        return observation
    last_input_at = interaction.get("last_input_at")
    if last_input_at is None:
        observation["status"] = "no_input_after_delivery"
        return observation
    if not isinstance(last_input_at, str):
        return observation
    try:
        latest_input = datetime.fromisoformat(last_input_at)
        if latest_input.tzinfo is None:
            return observation
        latest_input = latest_input.astimezone(timezone.utc)
        if latest_input > observed:
            return observation
    except (TypeError, ValueError, OverflowError, OSError):
        return observation
    observation["status"] = ("input_after_delivery" if latest_input > delivered
                             else "no_input_after_delivery")
    if observation["status"] == "input_after_delivery":
        observation["input_at"] = last_input_at
    return observation


def _thought_origin(data):
    for key in ("automatic_origin", "diagnostic_origin"):
        origin = data.get(key)
        if type(origin) is not dict:
            continue
        for event, label in THOUGHT_ORIGINS.items():
            if event not in origin:
                continue
            if event == "relationship_event" and type(origin[event]) is dict:
                purpose = origin[event].get("purpose")
                if purpose is None and origin[event].get("mode") == "slot":
                    purpose = data.get("purpose")
                if not isinstance(purpose, str):
                    purposes = origin[event].get("purposes")
                    purpose = purposes[0] if isinstance(purposes, list) and len(purposes) == 1 else None
                return PURPOSES.get(purpose, label) if isinstance(purpose, str) else label
            if event == "news_event":
                provenance = data.get("news_provenance")
                category = _text(provenance.get("category"), 48) if type(provenance) is dict else ""
                return label + (" · " + category if category else "")
            return label
        if isinstance(origin.get("batch_id"), str) and origin["batch_id"]:
            return "近期对话"

    sources = data.get("sources")
    if isinstance(sources, list):
        labels = []
        for source in sources:
            owner = source.get("owner") if type(source) is dict else None
            label = SOURCE_LABELS.get(owner) if isinstance(owner, str) else None
            if label and label not in labels:
                labels.append(label)
        if labels:
            return "、".join(labels[:2])
    return "主动分享"


def _contact_history(schedule, context, *, interaction=None, observed_at=None,
                    created_at_start=None, created_at_end=None):
    runtime = getattr(schedule, "runtime", None)
    snapshot = getattr(runtime, "read_only_snapshot", None)
    origin_key = getattr(context, "key", None)
    if not isinstance(origin_key, str) or not origin_key:
        return [], 0
    if not callable(snapshot):
        return [], 0

    conditions = ["origin_key = ?", "(body != '' OR (json_valid(record_json) AND "
        "json_extract(record_json, '$.decision') = 'skip'))"]
    parameters = [origin_key]
    if created_at_start is not None:
        conditions.append("created_at >= ?")
        parameters.append(created_at_start.isoformat(timespec="seconds"))
    if created_at_end is not None:
        conditions.append("created_at < ?")
        parameters.append(created_at_end.isoformat(timespec="seconds"))
    where = " AND ".join(conditions)
    limit = "" if created_at_start is not None and created_at_end is not None else " LIMIT 128"
    with snapshot() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM proactive_contacts WHERE {where}",
                                   parameters).fetchone()[0]
        contacts = connection.execute(f"""SELECT contact_id, record_json, body, payload_hash, created_at
            FROM proactive_contacts WHERE {where}
            ORDER BY created_at DESC, contact_id DESC{limit}""", parameters).fetchall()
        if not contacts:
            return [], total
        contact_ids = tuple(row["contact_id"] for row in contacts)
        placeholders = ",".join("?" for _ in contact_ids)
        deliveries = connection.execute(f"""SELECT request_json, payload_hash, state, receipt_ref, delivered_at
            FROM background_deliveries WHERE owner = ? AND origin_key = ?
              AND json_extract(request_json, '$.source_ref') IN ({placeholders})""",
            (CONTACT_OWNER, origin_key, *contact_ids)).fetchall()

    by_contact = {contact_id: [] for contact_id in contact_ids}
    for delivery in deliveries:
        try:
            request = json.loads(delivery["request_json"])
            contact_id = request.get("source_ref")
            if (request.get("owner") != CONTACT_OWNER or request.get("logical_key") != contact_id
                    or contact_id not in by_contact):
                continue
            by_contact[contact_id].append((request, delivery))
        except (TypeError, ValueError, AttributeError):
            continue

    items = []
    for row in contacts:
        try:
            data = json.loads(row["record_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if not row["body"] and data.get("decision") != "skip":
            continue
        parts = data.get("parts")
        if data.get("decision") == "skip" and not row["body"]:
            parts = []
        elif not isinstance(parts, list) or not parts:
            parts = [{"text": row["body"], "payload_hash": row["payload_hash"]}]
        if len(parts) > 64:
            continue

        records, delivery_states = [], []
        seen_sequences = set()
        for request, delivery in by_contact[row["contact_id"]]:
            sequence = request.get("sequence", 0)
            part_count = request.get("part_count", 1)
            if (type(sequence) is not int or type(part_count) is not int
                    or not 0 <= sequence < len(parts) or part_count != len(parts)):
                continue
            part = parts[sequence]
            if (not isinstance(part, dict) or request.get("payload_hash") != part.get("payload_hash")
                    or delivery["payload_hash"] != part.get("payload_hash")):
                continue
            delivery_states.append(delivery["state"])
            delivered_at = delivery["delivered_at"]
            receipt_ref = delivery["receipt_ref"]
            if (delivery["state"] != "delivered" or sequence in seen_sequences
                    or not isinstance(delivered_at, str) or not isinstance(receipt_ref, str) or not receipt_ref):
                continue
            try:
                datetime.fromisoformat(delivered_at)
            except ValueError:
                continue
            seen_sequences.add(sequence)
            records.append({"sequence": sequence, "delivered_at": delivered_at,
                            "text": _text(part.get("text"), 360)})

        delivered_count = len(records)
        if parts and delivered_count == len(parts):
            status = "delivered"
        elif delivered_count:
            status = "partial"
        elif any(state in ("dispatched", "accepted", "unknown") for state in delivery_states):
            status = "unconfirmed"
        elif "pending" in delivery_states or data.get("decision") == "send":
            status = "pending"
        elif "failed" in delivery_states:
            status = "failed"
        elif "cancelled" in delivery_states:
            status = "cancelled"
        elif data.get("decision") == "skip":
            status = "not_sent"
        else:
            status = "reviewing"

        records.sort(key=lambda item: item["sequence"])
        used_at = records[-1]["delivered_at"] if records else None
        created_at = row["created_at"]
        summary = _text(data.get("thought_summary"), 120)
        purpose = data.get("purpose")
        purpose_label = PURPOSES.get(purpose, purpose) if isinstance(purpose, str) else ""
        usage = {"status": status, "delivered_parts": delivered_count,
            "total_parts": len(parts), "last_used_at": used_at, "records": records,
            "thought_origin": _thought_origin(data)}
        if delivered_count == len(parts) and used_at is not None and observed_at is not None:
            usage["followup_observation"] = _response_observation(interaction, used_at, observed_at)
        skip_reason = data.get("skip_reason")
        model_skip_reason = data.get("model_skip_reason")
        items.append({"id": digest([CONTACT_OWNER, row["contact_id"]])[:20],
            "category": "未发送的念头" if status == "not_sent" else "主动消息",
            "purpose": _text(purpose_label, 80),
            "title": summary or "主动联系记录", "detail": _text(row["body"], 2000),
            "skip_reason": _text(skip_reason, 48) if isinstance(skip_reason, str) else "",
            "model_skip_reason": _text(model_skip_reason, 48) if isinstance(model_skip_reason, str) else "",
            "note": "", "source": "主动念头记录" if status == "not_sent" else "主动消息记录",
            "captured_at": created_at,
            "expires_at": None, "usage": usage})
    return items, total


def read_proactive_contact_traces(schedule, contexts, *, observed_at,
                                  created_at_start=None, created_at_end=None):
    """Project pre-dispatch contact drafts and their original delivery receipts."""
    items, total, contexts_read = [], 0, 0
    history = getattr(getattr(schedule, "contacts", None), "history", None)
    for context, target_label, role_label in tuple(contexts)[:128]:
        if context.scope.kind is not ScopeKind.PRIVATE:
            continue
        interaction = None
        reader = getattr(history, "interaction", None)
        if callable(reader):
            try:
                interaction = reader(context, observed_at, action=CONTACT_OWNER)
            except (TaskError, KeyError, TypeError, ValueError, UnicodeError, OSError, AttributeError):
                interaction = None
        rows, count = _contact_history(schedule, context, interaction=interaction, observed_at=observed_at,
            created_at_start=created_at_start, created_at_end=created_at_end)
        contexts_read += 1
        total += count
        for item in rows:
            item["scope"] = "window"
            item["trace_window_id"] = digest([getattr(context, "key", "")])[:24]
            item["trace_target"] = str(target_label or "私聊目标")[:120]
            item["trace_role"] = str(role_label or "角色")[:80]
            items.append(item)
    items.sort(key=lambda item: (item["captured_at"], item["id"]), reverse=True)
    bounded = created_at_start is not None and created_at_end is not None
    projected = items if bounded else items[:128]
    return {"observed_at": observed_at.isoformat(), "items": projected,
            "count": len(projected), "total_count": total, "contexts_read": contexts_read}


def read_material_pool(schedule, context, *, observed_at, settings):
    """Return only current, recipient-readable materials for one private target."""
    if context.scope.kind is not ScopeKind.PRIVATE:
        return {"observed_at": observed_at.isoformat(), "items": [], "count": 0}
    records = schedule.contacts.sources.list_readable_current(
        context.scope,
        owners=tuple(SOURCE_CATEGORIES),
        expected_recipients=context.audience.recipients,
        resolved=context.actor,
        capability="text",
        observed_at=observed_at,
        authorizer=schedule.tasks.identity,
    )
    items = [item for record in records
             if (item := _record_projection(record, settings, observed_at)) is not None]
    conversation = _latest_conversation(schedule, context, observed_at, settings)
    if conversation is not None:
        items.append(conversation)
    interaction = None
    history = getattr(getattr(schedule, "contacts", None), "history", None)
    reader = getattr(history, "interaction", None)
    if callable(reader):
        try:
            interaction = reader(context, observed_at, action=CONTACT_OWNER)
        except (TaskError, KeyError, TypeError, ValueError, UnicodeError, OSError, AttributeError):
            interaction = None
    contact_history, history_count = _contact_history(
        schedule, context, interaction=interaction, observed_at=observed_at)
    items.extend(contact_history)
    items.sort(key=lambda item: (item["captured_at"], item["id"]), reverse=True)
    return {"observed_at": observed_at.isoformat(), "items": items[:128], "count": len(items[:128]),
            "history_count": history_count}


def read_shared_material_pool(schedule, context, *, observed_at, settings):
    """Read role-wide public discoveries without projecting private window data."""
    events = getattr(schedule, "news_events", None)
    if events is None or context.scope.kind is not ScopeKind.PRIVATE:
        return {"observed_at": observed_at.isoformat(), "checked_at": None,
                "state": "unavailable", "failure": None, "items": [], "count": 0}
    snapshot = events.shared_materials(context, settings, observed_at)
    items = snapshot.get("items", []) if type(snapshot) is dict else []
    return {"observed_at": observed_at.isoformat(),
            "checked_at": snapshot.get("checked_at"),
            "state": snapshot.get("state", "unavailable"),
            "failure": snapshot.get("failure"),
            "items": items, "count": len(items)}


def read_role_material_pool(schedule, *, observed_at, settings):
    """Read public role discoveries without requiring a selected private window."""
    events = getattr(schedule, "news_events", None)
    if events is None:
        snapshot = {"state": "unavailable", "checked_at": None, "failure": None, "items": []}
    else:
        snapshot = events.role_materials(settings, observed_at)
    items = snapshot.get("items", []) if type(snapshot) is dict else []
    items = [{**item, "scope": "role"} for item in items if type(item) is dict][:128]
    return {"observed_at": observed_at.isoformat(),
            "checked_at": snapshot.get("checked_at") if type(snapshot) is dict else None,
            "state": snapshot.get("state", "unavailable") if type(snapshot) is dict else "unavailable",
            "failure": snapshot.get("failure") if type(snapshot) is dict else None,
            "relationship_count": 0, "history_count": 0,
            "shared_count": len(items), "items": items, "count": len(items)}


def read_active_material_pool(schedule, context, *, observed_at, settings):
    """Unify current relationship materials with public role-wide discoveries."""
    relationship = read_material_pool(schedule, context, observed_at=observed_at, settings=settings)
    shared = read_shared_material_pool(schedule, context, observed_at=observed_at, settings=settings)
    items, seen_urls = [], set()
    for item in (*shared.get("items", ()), *relationship.get("items", ())):
        if type(item) is not dict:
            continue
        url = item.get("url")
        if type(url) is str and url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        items.append(item)

    def captured_at(item):
        try:
            return datetime.fromisoformat(item.get("captured_at", "")).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            return 0

    items.sort(key=lambda item: (captured_at(item), item.get("id", "")), reverse=True)
    items = items[:128]
    return {"observed_at": observed_at.isoformat(),
            "checked_at": shared.get("checked_at"),
            "state": shared.get("state", "unavailable"),
            "failure": shared.get("failure"),
            "relationship_count": relationship.get("count", 0),
            "history_count": relationship.get("history_count", 0),
            "shared_count": shared.get("count", 0),
            "items": items, "count": len(items)}
