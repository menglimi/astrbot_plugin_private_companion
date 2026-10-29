"""Handle authenticated OneBot poke notices on their original native route."""

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timezone

from companion_v2.adapters.astrbot.native import NativeConversationInput, account_number
from companion_v2.conversation.quotes.models import message_number
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.storage.repositories import InboxRepository

from .recalls import authenticate_notice_route, is_notice_event


CONSUMER = "conversation.native_poke.v1"
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


def _spawn_context_stream(controller, route, context, instruction, event_key, happened_at):
    """Schedule the bounded context stream B after a configurable delay.

    Stream A stays exactly on the release path.  Stream B never raises: a
    failed or skipped context reply is silently dropped without touching the
    poke receipt, the stream A outcome, or the native acknowledgement.
    """
    policy = controller.poke_policy
    if (not policy.context_reply_enabled or route.kind is not ScopeKind.GROUP
            or not 0 <= int(policy.context_delay_seconds) <= 30):
        return
    logger.info("[CompanionV2][poke-context] 流B 已启动，延迟 %d 秒", policy.context_delay_seconds)
    task = asyncio.create_task(_poke_context_stream(controller, route, context, instruction,
        event_key + ":context", happened_at, policy.context_delay_seconds))
    applications = getattr(controller.session.application, "poke_context_tasks", None)
    if isinstance(applications, set):
        applications.add(task)
        task.add_done_callback(applications.discard)


async def _poke_context_stream(controller, route, context, instruction, event_key, happened_at, delay):
    try:
        await asyncio.sleep(delay)
        application = controller.session.application
        if (controller._terminated or application.state != "running"
                or not controller.poke_policy.enabled or not controller.poke_policy.context_reply_enabled):
            return
        observed_at = datetime.fromtimestamp(happened_at, timezone.utc)
        # Reuse the poke-time context verbatim: the task ledger rejects a
        # re-authorized context whose authority inputs changed during the
        # delay, and re-authenticating here would rebuild exactly that.
        recent_lines = _bounded_recent_lines(controller, context, controller.now())
        generated = await application.poke_reply_model.reply(
            context, instruction, event_key, mode="context",
            recent_context=recent_lines)
        if not generated or not generated.strip():
            import logging; logging.getLogger(__name__).warning("[B4] empty generated")
            return
        api = _transport(controller, route)
        try:
            await api.call_action("send_group_msg", self_id=route.login,
                **{"group_id": int(route.peer)},
                message=[{"type": "text", "data": {"text": generated.strip()}}])
            logger.info("[CompanionV2][poke-context] 已发送接话回复: %s", generated.strip()[:60])
        except Exception as send_error:
            logger.warning("[CompanionV2][poke-context] 发送失败: %s", send_error)
            return
    except asyncio.CancelledError:
        return
    except TaskError as stream_error:
        logger.warning("[CompanionV2][poke-context] 流B 未完成: %s", stream_error)
        return
    except Exception:
        logger.warning("[CompanionV2][poke-context] 流B 异常退出", exc_info=True)
        return


def _bounded_recent_lines(controller, context, at):
    """Bounded recent group observations: at most the last 8, 160 chars each."""
    participation = getattr(getattr(controller.session.application, "tools", None),
                            "group_participation", None)
    if participation is None:
        return ""
    items, _turns = participation.admission.native_participation_history(context, "", at=at)
    recent_lines = []
    for item in items[-8:]:
        body = str(item.get("text", ""))[:160]
        if not body:
            continue
        speaker = str(item.get("speaker_id", "")) or "群友"
        recent_lines.append(f"{speaker}: {body}")
    if not recent_lines:
        return ""
    return ("以下是拍一拍发生几秒后群里的最近聊天记录（按时间顺序，speaker_id 为发送者账号）：\n"
            + "\n".join(recent_lines))


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
            # Stream B starts on every accepted poke regardless of stream A's dice.
            _spawn_context_stream(controller, route, context, instruction, key, happened_at)
            reply = controller.poke_policy.reply_for(key)
            if reply is None:
                return Outcome.success({"state": "skipped", "replayed": False})
            response_kind = "preset"
            if controller.poke_policy.use_model_reply(key):
                try:
                    generated = await controller.session.application.poke_reply_model.reply(context, instruction, key)
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
