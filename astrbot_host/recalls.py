"""Authenticate native OneBot notices before forwarding withdrawal facts."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging

from companion_v2.adapters.astrbot.native import (
    account_number, event_account_id, event_peer_id, native_client_connected,
    platform_client_matches,
)
from companion_v2.conversation.quotes.models import message_number
from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind


logger = logging.getLogger(__name__)


def is_notice_event(event):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    return isinstance(raw, Mapping) and raw.get("post_type") == "notice"


def has_post_type(event, expected):
    """同一个原生事件在 notice 与 message 两种形态下字段一样，只有 post_type 不同。

    路由认证的其余部分（账号、平台、客户端、会话来源）对两者完全一致，所以调用方
    用这个参数说明自己在解哪种事件，而不是各写一份认证。
    """
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    return isinstance(raw, Mapping) and raw.get("post_type") == expected


@dataclass(frozen=True)
class NativeNoticeRoute:
    kind: ScopeKind
    platform_id: str
    bot_id: str
    login: str
    sender: str
    peer: str
    session_id: str
    client: object
    group_id: str = ""
    platform: object | None = None
    platform_name: str = ""


def _lark_private_message_route(controller, event):
    message, meta, native_session = event.message_obj, event.platform_meta, event.session
    raw = getattr(message, "raw_message", None)
    if (getattr(raw, "chat_type", None) != "p2p"
            or getattr(raw, "message_id", None) != message.message_id):
        return None
    login, sender = event_account_id(event), event_peer_id(event)
    settings = controller.settings
    if hasattr(settings, "targets"):
        target = settings.private_target(meta.id, login, sender)
        if target is None:
            return None
        bot_id = target.bot_id
    else:
        scope = controller.configuration.scope if controller.configuration is not None else None
        if scope is None or (meta.id, login, sender) != (
                scope.platform, scope.account_id, scope.native_scope_id):
            return None
        bot_id = scope.bot_id
    bridge = controller.session.bridge
    account = bridge.accounts.get((meta.id, login))
    pinned = bridge.platforms.get(meta.id)
    if account is None or account.bot_id != bot_id or pinned is None:
        raise TaskError("host_recall_route", "message account is outside the pinned native bridge")
    platform, client = pinned
    if (controller.context.get_platform_inst(meta.id) is not platform
            or platform.get_client() is not client or platform.meta().id != meta.id
            or platform.meta().name != "lark" or not platform_client_matches(platform, event.bot)
            or not native_client_connected(platform, login)):
        raise TaskError("host_recall_account_changed", "message must come from the current connected platform")
    if (getattr(message.type, "value", None) != "FriendMessage" or message.group_id
            or getattr(native_session.message_type, "value", None) != "FriendMessage"
            or native_session.platform_id != meta.id or native_session.platform_name != meta.id
            or native_session.session_id != sender
            or event.unified_msg_origin != f"{meta.id}:FriendMessage:{sender}"):
        raise TaskError("host_recall_route", "message fields do not identify one original private route")
    return NativeNoticeRoute(ScopeKind.PRIVATE, meta.id, bot_id, login, sender, sender,
                             native_session.session_id, client, platform=platform, platform_name="lark")


def authenticate_notice_route(controller, event, kind, *, expected_post="notice"):
    session = controller.session
    if (session is None or controller._terminated or session.application.state != "running"
            or not isinstance(event, controller.native_types.event)):
        return None
    message, meta, native_session = event.message_obj, event.platform_meta, event.session
    if meta.name == "lark" and kind is ScopeKind.PRIVATE and expected_post == "message":
        return _lark_private_message_route(controller, event)
    if not has_post_type(event, expected_post):
        return None
    raw = message.raw_message
    login, sender = account_number(raw.get("self_id")), account_number(raw.get("user_id"))
    peer = account_number(raw.get("group_id")) if kind is ScopeKind.GROUP else sender
    settings = controller.settings
    if hasattr(settings, "targets"):
        target = (settings.group_target(meta.id, login, peer)
                  if kind is ScopeKind.GROUP
                  else settings.private_target(meta.id, login, peer))
        if target is None:
            return None
        bot_id = target.bot_id
    else:
        configuration = controller.configuration
        if configuration is None:
            return None
        scope = configuration.scope
        if kind is not ScopeKind.PRIVATE or (meta.id, login, peer) != (scope.platform, scope.account_id, scope.native_scope_id):
            return None
        bot_id = scope.bot_id
    bridge = session.bridge.groups if kind is ScopeKind.GROUP else session.bridge
    account = bridge.accounts.get((meta.id, login))
    pinned = bridge.platforms.get(meta.id)
    if account is None or account.bot_id != bot_id or pinned is None:
        raise TaskError("host_recall_route", "notice account is outside the pinned native bridge")
    platform, client = pinned
    if (controller.context.get_platform_inst(meta.id) is not platform or platform.get_client() is not client
            or event.bot is not client or platform.meta().id != meta.id or platform.meta().name != "aiocqhttp"
            or meta.name != "aiocqhttp" or not client._wsr_api_clients.get(login)):
        raise TaskError("host_recall_account_changed", "notice must come from the current connected platform and client")
    event_kind = "GroupMessage" if kind is ScopeKind.GROUP else "FriendMessage"
    # QQ 群临时会话是 message_type=private、sub_type=group，原始事件仍带 group_id。
    # 它仍然是私聊，只是平台要求发送时带上来源群。
    private_group = ""
    if kind is ScopeKind.PRIVATE and raw.get("sub_type") == "group":
        raw_group = raw.get("group_id")
        if raw_group not in (None, "", 0, "0"):
            private_group = account_number(raw_group)
    expected_sessions = (peer, sender + "_" + peer) if kind is ScopeKind.GROUP else (peer,)
    if (account_number(message.self_id) != login or account_number(message.sender.user_id) != sender
            or getattr(message.type, "value", None) != event_kind
            or getattr(native_session.message_type, "value", None) != event_kind
            or native_session.platform_id != meta.id or native_session.platform_name != meta.id
            or native_session.session_id not in expected_sessions
            or event.unified_msg_origin != f"{meta.id}:{event_kind}:{native_session.session_id}"
            or (kind is ScopeKind.GROUP and (account_number(message.group_id) != peer
                                           or str(message.session_id) != peer))
            or (kind is ScopeKind.PRIVATE
                and (message.group_id or raw.get("group_id")) and not private_group)):
        raise TaskError("host_recall_route", "notice fields do not identify one original native route")
    return NativeNoticeRoute(kind, meta.id, bot_id, login, sender, peer,
                             native_session.session_id, client, private_group)


def capture_recall(controller, event):
    if not is_notice_event(event):
        return None
    raw = event.message_obj.raw_message
    notice_type = raw.get("notice_type")
    if notice_type not in ("friend_recall", "group_recall"):
        return None
    kind = ScopeKind.GROUP if notice_type == "group_recall" else ScopeKind.PRIVATE
    route = authenticate_notice_route(controller, event, kind)
    if route is None:
        return None
    # SDK assigns the notice itself a UUID. Only the raw notice identifies the
    # withdrawn message; operator_id is the moderator, never the original author.
    identifier = message_number(raw.get("message_id") or raw.get("msg_id"), error_code="host_recall_invalid")
    return MessageRecall(controller.session.runtime.installation_id, route.platform_id, route.bot_id,
                         route.login, route.kind, route.peer, identifier, route.sender)


async def receive_notice(controller, event):
    if not is_notice_event(event):
        return None
    async with controller._control_lock:
        try:
            record = capture_recall(controller, event)
            if record is None:
                return None
            result = controller.session.application.platform_recall(record)
            if result.failure is None:
                counts = result.unwrap()
                logger.info(
                    "[CompanionV2] 已记录消息撤回 notice=%s scope=%s message_id=%s "
                    "withdrawn_inputs=%s revoked_quotes=%s recalled_replies=%s cancelled=%s",
                    record.kind.value, record.native_scope_id, record.message_id,
                    counts.get("withdrawn_inputs", 0), counts.get("revoked_quotes", 0),
                    counts.get("recalled_replies", 0), counts.get("cancelled", 0))
            return result
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except (ValueError, TypeError, AttributeError, KeyError):
            return Outcome.failed("host_recall_invalid", "native recall notice could not be authenticated")
