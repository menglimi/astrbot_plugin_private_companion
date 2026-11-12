"""Run the optional Laya classifier before AstrBot wakes its reply model."""

import asyncio
from datetime import datetime, timezone
import logging
from time import perf_counter

from companion_v2.decision.conversation import decision_advice
from companion_v2.decision.store import LayaSettingsError
from companion_v2.execution.models import require, stamp
from .group_admission import group_trigger_facts


logger = logging.getLogger(__name__)


def _recent_private_messages(application, context, event_id, at):
    """Read a small, authorized history window and keep the current turn separate."""
    try:
        from companion_v2.conversation.private_history import PrivateConversationHistory

        history = PrivateConversationHistory(application.background_history)
        snapshot = history.capture(context, at, limit=6, max_chars=2400,
                                  exclude_event_id=event_id, action="conversation.reply")
        items = history.read(snapshot, context, at, action="conversation.reply")
    except Exception as error:
        logger.debug("[CompanionV2] Laya 私聊历史暂不可用 error_type=%s", type(error).__name__)
        return []
    recent, remaining = [], 1800
    for item in items:
        role = item.get("role")
        body = str(item.get("text", "")).strip()
        if role not in ("user", "assistant") or not body or remaining <= 0:
            continue
        body = body[:min(600, remaining)]
        recent.append({"role": role, "text": body,
                       "at": item.get("received_at") or item.get("delivered_at") or ""})
        remaining -= len(body)
    return recent


def _recent_group_bot_messages(application, context, event_id, at):
    """Read only this authorized group's actual bot send returns."""
    try:
        messages = application.messages
        require(messages.tasks.authorize_context(context, action="conversation.reply", observed_at=at))
        from companion_v2.conversation.native_history import body, reference, rows

        candidates = rows(messages.runtime, context, end=stamp(at), kind="assistant", limit=8)
        recent = []
        remaining = 1600
        for row in reversed(candidates):
            if event_id and row["event_id"] == event_id:
                continue
            item = body(messages, reference(row), context, at)
            text = str(item.get("text", "")).strip()
            if not text or remaining <= 0:
                continue
            text = text[:min(500, remaining)]
            recent.append({"text": text, "returned_at": item.get("delivered_at", "")})
            remaining -= len(text)
        return recent
    except Exception as error:
        logger.debug("[CompanionV2] Laya 群聊回复历史暂不可用 error_type=%s", type(error).__name__)
        return []


def _role_state_summary(application, context):
    """Read a brief, authorized role-state summary so Laya tone matches role energy/mood."""
    if application is None or context is None:
        return None
    role_state = getattr(application, "role_state", None)
    if role_state is None:
        return None
    try:
        view = role_state.read(context)
    except Exception:
        return None
    values = view.get("values") if isinstance(view, dict) else None
    if not isinstance(values, dict):
        return None
    summary = {}
    if isinstance(values.get("energy"), int):
        summary["energy"] = values["energy"]
    for key in ("sleep", "busy"):
        if isinstance(values.get(key), str) and values[key].strip():
            summary[key] = values[key].strip()[:80]
    mood_val = values.get("mood")
    if isinstance(mood_val, str) and mood_val.strip():
        summary["mood"] = mood_val.strip()[:80]
    elif isinstance(mood_val, dict):
        desc = mood_val.get("description")
        if isinstance(desc, str) and desc.strip():
            summary["mood"] = desc.strip()[:80]
    return summary or None


def core_decision_facts(event, prepared, application, *, at):
    native, context = prepared.get("native_input"), prepared.get("context")
    scope = getattr(context, "scope", None)
    account = str(getattr(scope, "account_id", "") or "")
    sender = str(getattr(getattr(native, "principal", None), "native_account_id", "") or "")
    text = str(prepared.get("text", getattr(native, "text", getattr(event, "message_str", ""))) or "")
    text = getattr(native, "group_message_text", None) or text
    trigger = prepared.get("group_trigger")
    if trigger is None:
        trigger = group_trigger_facts(event, account)
    kinds = {getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
             for part in getattr(event.message_obj, "message", ())}
    has_image = bool(trigger["has_image"] or getattr(native, "images", ()))
    has_audio = bool(getattr(native, "audios", ()) or kinds & {"Record", "record", "audio"})
    has_video = bool(getattr(native, "videos", ()) or kinds & {"Video", "video"})

    def quotes_bot(value, depth=0):
        if depth > 4:
            return False
        if account and str(getattr(value, "sender_id", "")) == account:
            return True
        return any(quotes_bot(child, depth + 1) for name in ("quotes", "forwards", "nodes")
                   for child in (getattr(value, name, ()) or ()))

    facts = {
        "scope_kind": prepared.get("channel"),
        "message_kind": "text" if text else "image" if has_image else "audio" if has_audio else "video" if has_video else "text",
        "user_text": text[:12000], "speaker_id": sender, "bot_account_id": account,
        "is_bot_message": bool(account and sender == account),
        "quoted_bot_message": trigger["quoted_bot"] or any(quotes_bot(item) for name in ("quotes", "forwards")
            for item in (getattr(native, name, ()) or ())),
        "mentioned": trigger["mentioned"], "native_wakeup": trigger["native_wakeup"],
        "mentions_others": trigger["mentions_others"], "has_image": has_image,
        "has_audio": has_audio, "has_video": has_video,
        "companion_role_id": str(getattr(getattr(getattr(context, "role_binding", None), "role", None), "subject_id", "")),
    }
    if prepared.get("channel") == "group":
        group_admission = prepared.get("group_admission") or {}
        facts["candidate_reason"] = group_admission.get("reason", "")
        if isinstance(group_admission.get("continuation"), dict):
            facts["group_continuation"] = dict(group_admission["continuation"])
        facts["group_participation_policy"] = dict(group_admission.get("participation_policy") or {})
        if isinstance(group_admission.get("question_reference"), dict):
            facts["question_reference"] = dict(group_admission["question_reference"])
        participation = getattr(getattr(application, "tools", None), "group_participation", None)
        if participation is not None:
            items, turns = participation.admission.native_participation_history(
                context, prepared.get("instruction", ""), at=at)
            remaining, recent = 6000, []
            current_event_id = prepared.get("event_id") or getattr(native, "event_id", None)
            for item in reversed(items[-12:]):
                if current_event_id and item.get("event_id") == current_event_id:
                    continue
                body = str(item.get("text", ""))[:min(800, remaining)]
                if not body:
                    continue
                recent.append({"speaker_id": item.get("speaker_id"), "text": body,
                               "received_at": item.get("received_at", "")})
                remaining -= len(body)
                if remaining <= 0:
                    break
            facts["recent_group_messages"] = list(reversed(recent))
            facts["recent_group_bot_messages"] = _recent_group_bot_messages(
                application, context, current_event_id, at)
            from companion_v2.conversation.group_observation import parse_moment
            facts["recent_bot_send_returns"] = [{"addressed_speaker_id": turn.get("speaker_id"),
                "returned_at": turn.get("returned_at"),
                "seconds_ago": max(0, round((at - parse_moment(turn["returned_at"])).total_seconds()))}
                for turn in turns[-3:] if parse_moment(turn.get("returned_at")) is not None]
    elif context is not None:
        facts["recent_private_messages"] = _recent_private_messages(
            application, context, prepared.get("event_id") or getattr(native, "event_id", None), at)
    state_summary = _role_state_summary(application, context)
    if state_summary:
        facts["role_state_summary"] = state_summary
    return facts


async def prepare_core_reply_decision(event, prepared, *, application, now=None):
    """Inference failures retain the ordinary authorized core reply path."""
    started = perf_counter()
    if prepared.get("channel") == "private":
        result = {"status": "skipped", "reason": "private_default_reply",
                  "applied_decision": {}, "suppress_reply": False,
                  "elapsed_ms": round((perf_counter() - started) * 1000)}
        prepared["laya_decision"] = result
        logger.info(
            "[CompanionV2] Laya 核心接话判断 event_ref=%s turn_id=%s channel=private "
            "status=skipped action=reply reason=private_default_reply elapsed_ms=%s",
            prepared.get("event_ref"), prepared.get("turn_id"), result["elapsed_ms"],
        )
        return result
    # A direct @ is an explicit invitation to speak.  Laya may classify the
    # text as "silent" (for example when the mention contains an image or a
    # short request), but that classification must not override the caller's
    # explicit wake-up.  Keep the decision record for diagnostics and skip the
    # extra inference entirely.
    if prepared.get("channel") == "group":
        admission = prepared.get("group_admission") or {}
        if admission.get("mentioned") is True:
            result = {
                "status": "skipped", "reason": "explicit_mention",
                "applied_decision": {"action": "reply"}, "suppress_reply": False,
                "elapsed_ms": round((perf_counter() - started) * 1000),
            }
            prepared["laya_decision"] = result
            logger.info(
                "[CompanionV2] Laya 核心接话判断 event_ref=%s turn_id=%s channel=group "
                "status=skipped action=reply reason=explicit_mention elapsed_ms=%s",
                prepared.get("event_ref"), prepared.get("turn_id"), result["elapsed_ms"],
            )
            return result
    resolver = getattr(application, "laya_decision_resolver", None)
    service = None
    result = {"status": "skipped", "reason": "laya_resolver_unavailable"}
    try:
        service = resolver(prepared.get("context")) if callable(resolver) else None
        if service is not None:
            trace = getattr(service, "set_trace", None)
            if callable(trace):
                trace(event_ref=prepared.get("turn_id"), source="conversation")
            at = getattr(prepared.get("native_input"), "at", None) or (now or (lambda: datetime.now(timezone.utc)))()
            facts = core_decision_facts(event, prepared, application, at=at)
            result = await service.decide(facts)
            if not isinstance(result, dict):
                raise ValueError("Laya service returned an invalid decision")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        result = {"status": "failed", "reason": getattr(error, "code", type(error).__name__)}
        # LayaSettingsError keeps the actionable cause in `reason` (for example
        # "mode 必须是 ..."); logging only the code turns a configuration
        # mistake into an opaque failure code. The detail never carries user
        # text, so it is safe next to the code.
        detail = getattr(error, "reason", "") if isinstance(error, LayaSettingsError) else ""
        if detail:
            result["reason_detail"] = detail
    active, instruction = decision_advice(result)
    result = {**result, "applied_decision": active, "suppress_reply": active.get("action") in ("wait", "silent")}
    record_application = getattr(service, "record_application", None)
    if callable(record_application):
        record_application({"applied_decision": active, "suppress_reply": result["suppress_reply"]})
    result.setdefault("elapsed_ms", round((perf_counter() - started) * 1000))
    prepared["laya_decision"] = result
    if instruction:
        prepared["prompt_instruction"] = (prepared.get("prompt_instruction", "") + "\n\n" + instruction).strip()
    logger.log(logging.WARNING if result["status"] == "failed" else logging.INFO,
        "[CompanionV2] Laya 核心接话判断 event_ref=%s turn_id=%s channel=%s status=%s action=%s reason=%s "
        "reason_detail=%s elapsed_ms=%s",
        prepared.get("event_ref"), prepared.get("turn_id"), prepared.get("channel"), result["status"],
        active.get("action", "not_applied"), result.get("reason", "semantic_classification"),
        result.get("reason_detail") or "-", result["elapsed_ms"])
    return result
