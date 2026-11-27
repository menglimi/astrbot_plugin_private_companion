"""Handle authenticated OneBot poke notices on their original native route."""

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from companion_v2.adapters.astrbot.native import NativeConversationInput, account_number
from companion_v2.conversation.group_observation import parse_moment
from companion_v2.conversation.quotes.models import message_number
from companion_v2.conversation.reset import boundary as reset_boundary
from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.storage.repositories import InboxRepository

from .recalls import authenticate_notice_route, is_notice_event


CONSUMER = "conversation.native_poke.v1"
POKE_REPLIES_PREFIX = "conversation.group_poke_replies.v1:"
POKE_WATERMARK_PREFIX = "conversation.group_poke_watermark.v1:"
CONTEXT_WINDOW_SECONDS = 90
MAX_POKE_REPLIES = 32
MAX_WATERMARK_IDS = 32
CONFLICT_LOOKBACK_SECONDS = 2
INFLIGHT_CONFLICT_WINDOW_SECONDS = 15
logger = logging.getLogger(__name__)


def is_poke_notice(event):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    return (is_notice_event(event) and raw.get("notice_type") == "notify"
            and raw.get("sub_type") == "poke")


def capture_poke(controller, event):
    if not is_poke_notice(event):
        return None
    raw = event.message_obj.raw_message
    kind = ScopeKind.GROUP if raw.get("group_id") is not None else ScopeKind.PRIVATE
    route = authenticate_notice_route(controller, event, kind)
    if route is None:
        return None
    if account_number(raw.get("target_id")) != route.login or route.sender == route.login:
        return None
    happened_at = raw.get("time")
    if type(happened_at) is not int or happened_at <= 0:
        raise TaskError("host_poke_invalid", "poke notice has no stable native event time")
    return route, happened_at


async def _authorize(controller, route, observed_at):
    session, settings = controller.session, controller.settings
    if hasattr(settings, "targets"):
        targets = settings.group_targets if route.kind is ScopeKind.GROUP else settings.targets
        target = (settings.group_target(route.platform_id, route.login, route.peer)
                  if route.kind is ScopeKind.GROUP
                  else settings.private_target(route.platform_id, route.login, route.peer))
        if target is None:
            raise TaskError("host_poke_route_changed", "poke target is no longer configured")
        if route.kind is ScopeKind.GROUP:
            # 正式版没有"预览/群投递授权"这两道闸：目标认得出来就能回。
            controller.authority.group_delivery.access(target)
            bridge = session.bridge.groups
            account = bridge.accounts[(route.platform_id, route.login)]
            conversation_id, role, instruction = await bridge._role(account, route.session_id)
            scope = ScopeRef(session.runtime.installation_id, route.platform_id, ScopeKind.GROUP, route.peer,
                             route.bot_id, route.login, conversation_id, route.session_id)
            actor = controller.authority.ensure_actor(target, route.sender)
            captured = NativeConversationInput("poke-" + digest([route.platform_id, route.login, route.peer,
                route.sender, observed_at]), controller.authority.principal(target, route.sender),
                ConversationRoute(scope, group_id=route.peer), group_audience(scope, actor.subject), "",
                observed_at, role, instruction)
            context = controller.authority.groups.select(target, captured)
            await bridge.verify_role(scope, context.role_binding, instruction)
        else:
            context, instruction = await session.synchronize(target)
            if (context.scope.kind is not route.kind
                    or (context.scope.platform, context.scope.account_id, context.scope.bot_id,
                        context.scope.native_scope_id)
                    != (route.platform_id, route.login, route.bot_id, route.peer)
                    or context.actor.principal.native_account_id != route.sender):
                raise TaskError("host_poke_route_changed", "poke target no longer matches its authenticated scope")
        session.authorize(context)
    else:
        session.require_delivery_authorization()
        context = session.profile.target
        scope = context.scope
        if (scope.kind is not route.kind
                or (scope.platform, scope.account_id, scope.bot_id, scope.native_scope_id)
                != (route.platform_id, route.login, route.bot_id, route.peer)
                or context.actor.principal.native_account_id != route.sender):
            raise TaskError("host_poke_route_changed", "poke target no longer matches the pinned scope")
        require(session.application.tasks.check_context_access(
            context, action="conversation.reply", observed_at=controller.now()))
        instruction = await session.bridge.verify_role(scope, context.role_binding)
    return context, instruction


def _transport(controller, route):
    client = route.client
    api = client._api._wsr_api
    if (type(api) is not controller.native_types.reverse_api or api._api_clients is not client._wsr_api_clients
            or not client._wsr_api_clients.get(route.login)):
        raise TaskError("host_poke_transport_changed", "native reverse-WebSocket account is unavailable")
    return api


async def _dispatch_guard(controller, event, captured):
    route, happened_at = captured
    await _authorize(controller, route, datetime.fromtimestamp(happened_at, timezone.utc))
    if capture_poke(controller, event) != captured:
        raise TaskError("host_poke_route_changed", "poke native route changed before dispatch")
    return _transport(controller, route)


def _poker_identity(route, event) -> str:
    """Return a concise, attributable label for whoever sent the poke."""
    message = getattr(event, "message_obj", None)
    raw = getattr(message, "raw_message", None)
    sender_obj = getattr(message, "sender", None)
    candidates = []
    if isinstance(raw, Mapping):
        raw_sender = raw.get("sender")
        if isinstance(raw_sender, Mapping):
            candidates.extend((raw_sender.get("card"), raw_sender.get("nickname")))
        candidates.extend((raw.get("user_card"), raw.get("nickname")))
    if sender_obj is not None:
        candidates.extend((getattr(sender_obj, "card", None), getattr(sender_obj, "nickname", None)))
    get_name = getattr(event, "get_sender_name", None)
    if callable(get_name):
        try:
            candidates.append(get_name())
        except Exception:
            pass
    for value in candidates:
        if isinstance(value, str):
            name = value.strip()[:40]
            if name and name != route.sender:
                return f"{name}({route.sender})"
    return route.sender


def _poke_scope_key(context) -> str:
    scope = context.scope
    return digest([
        scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
        scope.native_scope_id, scope.session_id,
        context.role_binding.role.subject_id, getattr(context.role_binding, "revision", 0),
    ])


def _read_poke_replies(runtime, context) -> list[dict]:
    row = runtime.read_one(
        "SELECT value FROM runtime_metadata WHERE key = ?",
        (POKE_REPLIES_PREFIX + _poke_scope_key(context),),
    )
    if row is None:
        return []
    try:
        saved = json.loads(row["value"])
        items = saved.get("items") if isinstance(saved, dict) else None
        if not isinstance(items, list):
            return []
    except (TypeError, ValueError):
        return []
    reset = reset_boundary(runtime, context.scope)
    if reset is None:
        return [dict(item) for item in items if isinstance(item, dict)]
    return [dict(item) for item in items
            if isinstance(item, dict) and str(item.get("sent_at", "")) > reset["reset_at"]]


def _record_poke_reply(runtime, context, *, text: str, sent_at: datetime, source: str) -> None:
    clean = str(text or "").strip()[:160]
    if not clean:
        return
    key = POKE_REPLIES_PREFIX + _poke_scope_key(context)
    try:
        with runtime.transaction() as connection:
            items = _read_poke_replies(runtime, context)
            items.append({"sent_at": stamp(sent_at), "text": clean, "source": source})
            connection.execute(
                """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, json.dumps({"version": 1, "items": items[-MAX_POKE_REPLIES:]},
                                 ensure_ascii=False, sort_keys=True)),
            )
    except Exception:
        logger.debug("[CompanionV2][poke-context] 记录戳一戳回复流水失败", exc_info=True)


def _read_context_watermark(runtime, context) -> tuple[datetime | None, set[str]]:
    row = runtime.read_one(
        "SELECT value FROM runtime_metadata WHERE key = ?",
        (POKE_WATERMARK_PREFIX + _poke_scope_key(context),),
    )
    if row is None:
        return None, set()
    try:
        saved = json.loads(row["value"])
        if not isinstance(saved, dict):
            return None, set()
        consumed_at = parse_moment(saved.get("consumed_at"))
        reset = reset_boundary(runtime, context.scope)
        if reset is not None and consumed_at is not None and stamp(consumed_at) <= reset["reset_at"]:
            return None, set()
        ids = {str(item) for item in (saved.get("event_ids") or ()) if item}
        return consumed_at, ids
    except (TypeError, ValueError):
        return None, set()


def _write_context_watermark(runtime, context, *, consumed_at: datetime, event_ids: list[str]) -> None:
    key = POKE_WATERMARK_PREFIX + _poke_scope_key(context)
    try:
        with runtime.transaction() as connection:
            prev_at, prev_ids = _read_context_watermark(runtime, context)
            final_at = consumed_at if prev_at is None or consumed_at >= prev_at else prev_at
            merged_ids = list(dict.fromkeys([*prev_ids, *event_ids]))[-MAX_WATERMARK_IDS:]
            connection.execute(
                """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, json.dumps({"version": 1, "consumed_at": stamp(final_at), "event_ids": merged_ids},
                                 ensure_ascii=False, sort_keys=True)),
            )
    except Exception:
        logger.debug("[CompanionV2][poke-context] 更新流B水位失败", exc_info=True)


def _observation_key(item: Mapping) -> str:
    event_id = item.get("event_id")
    if isinstance(event_id, str) and event_id:
        return event_id
    return digest([item.get("speaker_id", ""), item.get("received_at", ""), item.get("text", "")])


def _has_conflicting_core_reply(controller, context, instruction, observed_at: datetime, at: datetime) -> bool:
    """Return True when the main group reply path is active or just answered around this poke."""
    participation = getattr(getattr(controller.session.application, "tools", None),
                            "group_participation", None)
    if participation is None:
        return False
    admission = getattr(participation, "admission", None)
    if admission is None:
        return False
    try:
        live_map = getattr(admission, "_live_core_events", None)
        if isinstance(live_map, Mapping) and live_map.get(admission._key(context)):
            return True
        turns = admission._current_core_turns(context, instruction, at)
    except Exception:
        return False
    conflict_floor = observed_at - timedelta(seconds=CONFLICT_LOOKBACK_SECONDS)
    inflight_floor = observed_at - timedelta(seconds=INFLIGHT_CONFLICT_WINDOW_SECONDS)
    for turn in turns:
        state = turn.get("state")
        send_state = turn.get("send_state")
        if state in ("admitted", "inflight"):
            when = parse_moment(turn.get("inflight_at") or turn.get("admitted_at"))
            if when is not None and when >= inflight_floor:
                return True
            continue
        if send_state in ("started", "returned", "unknown") or turn.get("send_returns", 0) > 0:
            when = parse_moment(turn.get("returned_at") or turn.get("admitted_at"))
            if when is not None and when >= conflict_floor:
                return True
    return False


def _spawn_context_stream(controller, route, context, instruction, event_key, happened_at, poker_label):
    """Schedule the bounded context stream B after a configurable delay.

    Stream A stays exactly on the release path.  Stream B never raises: a
    skipped context reply is dropped, and a failed one is logged with its stable
    code, without touching the poke receipt, the stream A outcome, or the native
    acknowledgement. Rapid repeated pokes in the same group coalesce so only the
    latest pending stream B runs.
    """
    policy = controller.poke_policy
    if (not policy.context_reply_enabled or route.kind is not ScopeKind.GROUP
            or not 0 <= int(policy.context_delay_seconds) <= 30):
        return
    application = controller.session.application
    pending = getattr(application, "poke_context_pending", None)
    if not isinstance(pending, dict):
        pending = {}
        try:
            setattr(application, "poke_context_pending", pending)
        except Exception:
            pending = None
    group_key = (route.platform_id, route.login, route.peer)
    if pending is not None:
        previous = pending.get(group_key)
        if previous is not None and not previous.done():
            previous.cancel()
            logger.info("[CompanionV2][poke-context] 合并同群连续戳一戳，取消较早待执行的流B group=%s", route.peer)
    logger.info("[CompanionV2][poke-context] 流B 已启动，延迟 %d 秒", policy.context_delay_seconds)
    task = asyncio.create_task(_poke_context_stream(controller, route, context, instruction,
        event_key + ":context", happened_at, policy.context_delay_seconds, poker_label))
    if pending is not None:
        pending[group_key] = task

        def _cleanup_pending(done_task):
            if pending.get(group_key) is done_task:
                pending.pop(group_key, None)

        task.add_done_callback(_cleanup_pending)
    applications = getattr(application, "poke_context_tasks", None)
    if isinstance(applications, set):
        applications.add(task)
        task.add_done_callback(applications.discard)


async def _poke_context_stream(controller, route, context, instruction, event_key, happened_at, delay, poker_label):
    try:
        await asyncio.sleep(delay)
        application = controller.session.application
        if (controller._terminated or application.state != "running"
                or not controller.poke_policy.enabled or not controller.poke_policy.context_reply_enabled):
            return
        observed_at = datetime.fromtimestamp(happened_at, timezone.utc)
        now = controller.now()
        if _has_conflicting_core_reply(controller, context, instruction, observed_at, now):
            logger.info("[CompanionV2][poke-context] 检测到主群聊回复正在处理或刚完成，跳过流B group=%s", route.peer)
            return
        # Reuse the poke-time context verbatim: the task ledger rejects a
        # re-authorized context whose authority inputs changed during the
        # delay, and re-authenticating here would rebuild exactly that.
        recent_lines, watermark_meta = _bounded_recent_lines(
            controller, context, instruction, now,
            observed_at=observed_at, poker_id=route.sender, poker_label=poker_label,
        )
        if not recent_lines:
            logger.info("[CompanionV2][poke-context] 近期窗口内无未回复新消息，跳过流B模型调用 group=%s", route.peer)
            return
        generated = await application.poke_reply_model.reply(
            context, instruction, event_key, mode="context",
            recent_context=recent_lines, poker_label=poker_label)
        if not generated or not generated.strip():
            _write_context_watermark(controller.session.runtime, context, **watermark_meta)
            logger.info("[CompanionV2][poke-context] 模型判断无需接话（[SKIP]），保持安静")
            return
        if _has_conflicting_core_reply(controller, context, instruction, observed_at, controller.now()):
            _write_context_watermark(controller.session.runtime, context, **watermark_meta)
            logger.info("[CompanionV2][poke-context] 模型生成期间主群聊已接管回复，放弃发送流B group=%s", route.peer)
            return
        api = _transport(controller, route)
        clean_reply = generated.strip()
        try:
            await api.call_action("send_group_msg", self_id=route.login,
                **{"group_id": int(route.peer)},
                message=[{"type": "text", "data": {"text": clean_reply}}])
            _record_poke_reply(controller.session.runtime, context,
                               text=clean_reply, sent_at=controller.now(), source="poke_b")
            _write_context_watermark(controller.session.runtime, context, **watermark_meta)
            logger.info("[CompanionV2][poke-context] 已发送接话回复: %s", clean_reply[:60])
        except Exception as send_error:
            logger.warning("[CompanionV2][poke-context] 发送失败: %s", send_error)
            return
    except asyncio.CancelledError:
        return
    except TaskError as stream_error:
        logger.warning("[CompanionV2][poke-context] 流B 未完成 code=%s reason=%s",
                       stream_error.code, stream_error)
        return
    except Exception:
        logger.warning("[CompanionV2][poke-context] 流B 异常退出", exc_info=True)
        return


def _format_clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")


def _bounded_recent_lines(controller, context, instruction, at, *,
                          observed_at: datetime | None = None,
                          poker_id: str = "", poker_label: str = ""):
    """Build a strict short-window interleaved timeline for poke context stream B.

    Returns ``(prompt_block, watermark_meta)``. When there is no fresh,
    unanswered human message inside the sliding window, returns ``("", None)``
    so stream B skips the model call at zero token cost instead of re-echoing
    stale history.
    """
    participation = getattr(getattr(controller.session.application, "tools", None),
                            "group_participation", None)
    if participation is None:
        return "", None
    anchor = observed_at or at
    window_start = anchor - timedelta(seconds=CONTEXT_WINDOW_SECONDS)
    window_end = max(at, anchor) + timedelta(seconds=2)
    items, turns = participation.admission.native_participation_history(
        context, instruction, at=at, max_age_hours=1)
    runtime = controller.session.runtime
    poke_replies = _read_poke_replies(runtime, context)
    consumed_at, consumed_ids = _read_context_watermark(runtime, context)

    answer_moments = []
    for turn in turns:
        returned = parse_moment(turn.get("returned_at"))
        if returned is not None:
            answer_moments.append(returned)
    for entry in poke_replies:
        if entry.get("source") == "poke_b":
            sent = parse_moment(entry.get("sent_at"))
            if sent is not None:
                answer_moments.append(sent)
    latest_answer_at = max(answer_moments) if answer_moments else None

    window_humans = []
    unanswered_humans = []
    for item in items:
        when = parse_moment(item.get("received_at"))
        body = str(item.get("text", "")).strip()[:160]
        if when is None or not body:
            continue
        if not (window_start <= when <= window_end):
            continue
        obs_key = _observation_key(item)
        window_humans.append((when, item, body, obs_key))
        already_consumed = (obs_key in consumed_ids
                            or (consumed_at is not None and when < consumed_at))
        already_answered = latest_answer_at is not None and when <= latest_answer_at
        if not already_consumed and not already_answered:
            unanswered_humans.append((when, obs_key))

    if not unanswered_humans:
        return "", None

    timeline = []
    for when, item, body, _obs_key in window_humans[-8:]:
        speaker = str(item.get("speaker_id", "")) or "群友"
        if poker_id and speaker == poker_id:
            display = f"{poker_label or speaker}[戳你的人]"
        else:
            display = speaker
        timeline.append((when, 0, f"[{_format_clock(when)}] {display}: {body}"))

    for turn in turns[-4:]:
        returned = parse_moment(turn.get("returned_at"))
        if returned is not None and window_start <= returned <= window_end:
            target_speaker = str(turn.get("speaker_id", "") or "")
            summary = f"（已回复了 {target_speaker} 的消息）" if target_speaker else "（已发出群聊回复）"
            timeline.append((returned, 1, f"[{_format_clock(returned)}] 我: {summary}"))

    for entry in poke_replies[-4:]:
        sent = parse_moment(entry.get("sent_at"))
        body = str(entry.get("text", "")).strip()[:160]
        if sent is not None and body and window_start <= sent <= window_end:
            tag = "我(刚回应了戳一戳)" if entry.get("source") == "poke_a" else "我"
            timeline.append((sent, 1, f"[{_format_clock(sent)}] {tag}: {body}"))

    timeline.sort(key=lambda row: (row[0], row[1]))
    recent_lines = [line for _, _, line in timeline[-10:]]
    watermark_meta = {
        "consumed_at": max(when for when, _ in unanswered_humans),
        "event_ids": [obs_key for _, _, _, obs_key in window_humans],
    }
    poker_hint = f"本次戳一戳由「{poker_label or poker_id}」于 {_format_clock(anchor)} 发起。" if (poker_label or poker_id) else ""
    header = (
        f"{poker_hint}以下是拍一拍前后 {CONTEXT_WINDOW_SECONDS} 秒内群里的最新聊天时间线"
        "（按时间顺序）：\n"
    )
    return header + "\n".join(recent_lines), watermark_meta


async def receive_poke(controller, event):
    if not is_poke_notice(event) or not controller.poke_policy.enabled:
        return None
    async with controller._control_lock:
        try:
            captured = capture_poke(controller, event)
            if captured is None:
                return None
            route, happened_at = captured
            key = digest([controller.session.runtime.installation_id, route.platform_id, route.login,
                          route.kind.value, route.peer, route.sender, happened_at])
            receipt = require(InboxRepository(controller.session.runtime).accept(
                consumer=CONSUMER, event_id=key, payload_hash=digest([route.kind.value, route.peer,
                    route.sender, route.login, happened_at]), observed_at=controller.now()))
            if receipt.replayed:
                return Outcome.success({"state": "replayed", "replayed": True})
            observed_at = datetime.fromtimestamp(happened_at, timezone.utc)
            context, instruction = await _authorize(controller, route, observed_at)
            poker_label = _poker_identity(route, event)
            # Stream B starts on every accepted poke regardless of stream A's dice.
            _spawn_context_stream(controller, route, context, instruction, key, happened_at, poker_label)
            reply = controller.poke_policy.reply_for(key)
            if reply is None:
                return Outcome.success({"state": "skipped", "replayed": False})
            response_kind = "preset"
            if controller.poke_policy.use_model_reply(key):
                try:
                    generated = await controller.session.application.poke_reply_model.reply(
                        context, instruction, key, poker_label=poker_label)
                    if generated:
                        reply, response_kind = generated, "model"
                except (TaskError, ValueError, TypeError, AttributeError, KeyError) as error:
                    logger.warning("[CompanionV2] 戳一戳模型回复未完成，使用预设短句 error_type=%s code=%s",
                                   type(error).__name__, getattr(error, "code", ""))
            api = await _dispatch_guard(controller, event, captured)
            action = "send_group_msg" if route.kind is ScopeKind.GROUP else "send_private_msg"
            target = {"group_id" if route.kind is ScopeKind.GROUP else "user_id": int(route.peer)}
            try:
                result = await api.call_action(action, self_id=route.login, **target,
                    message=[{"type": "text", "data": {"text": reply}}])
            except Exception:
                return Outcome.success({"state": "unknown", "replayed": False, "poke_back": False,
                                        "response_kind": response_kind})
            identifier = result.get("message_id") if isinstance(result, Mapping) else None
            try:
                message_id = message_number(identifier, error_code="host_poke_ack_invalid")
            except TaskError:
                return Outcome.success({"state": "unknown", "replayed": False, "poke_back": False,
                                        "response_kind": response_kind})
            if route.kind is ScopeKind.GROUP:
                _record_poke_reply(controller.session.runtime, context,
                                   text=reply, sent_at=controller.now(), source="poke_a")
            if not controller.poke_policy.poke_back(key):
                return Outcome.success({"state": "delivered", "replayed": False, "poke_back": False,
                                        "message_id": message_id, "response_kind": response_kind})
            back_count = 0
            for _ in range(controller.poke_policy.back_times):
                try:
                    api = await _dispatch_guard(controller, event, captured)
                except (TaskError, ValueError, TypeError, AttributeError, KeyError):
                    return Outcome.success({"state": "delivered", "replayed": False,
                                            "poke_back": "partial" if back_count else False,
                                            "poke_back_count": back_count, "message_id": message_id,
                                            "response_kind": response_kind})
                try:
                    await api.call_action("group_poke" if route.kind is ScopeKind.GROUP else "friend_poke",
                        self_id=route.login,
                        **({"group_id": int(route.peer)} if route.kind is ScopeKind.GROUP else {}),
                        user_id=int(route.sender))
                except Exception:
                    return Outcome.success({"state": "delivered", "replayed": False, "poke_back": "unknown",
                                            "poke_back_count": back_count, "message_id": message_id,
                                            "response_kind": response_kind})
                back_count += 1
            return Outcome.success({"state": "delivered", "replayed": False, "poke_back": True,
                                    "poke_back_count": back_count, "message_id": message_id,
                                    "response_kind": response_kind})
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except (ValueError, TypeError, AttributeError, KeyError, StopIteration):
            return Outcome.failed("host_poke_invalid", "native poke notice could not be authenticated")
