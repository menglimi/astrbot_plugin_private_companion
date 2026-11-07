"""把"没有对话权限"那一句话真的发出去。

为什么这一句不走核心投递通道（``companion_v2/delivery``）：那条通道要求一次
已解析的 ``TaskContext`` 和一项按人签发的能力 grant（``DeliveryOwner.action``），
而我们要回话的对象恰恰是**没有**对话权限的人，甚至是**没有任何身份**的陌生号
（未绑定账号没有 subject、没有 scope、没有人格路由）。所以这句话在这里用平台
反向 WebSocket 直接发一次纯文本——与 ``pokes.py`` 里那半句回话同一种形状。

三条硬约束：

* **只在真实投递模式下才可能发**：现在"真实投递"是唯一模式（正式版已删除预览档），
  所以这句话总是允许发出；`private_notice_allowed` 是唯一的判断点。
* **只发这一行**：不读记忆、不调模型、不建身份、不签发 grant、不写对话表。
* **不重放未知结果**：平台调用抛错就是"没发出去"，记一次失败，不重试；
  冷却由 ``PrivateRefusalNotice`` 负责，避免同一个人被反复打扰。
"""

from __future__ import annotations

from collections.abc import Mapping

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind


async def send_text_notice(controller, route, *, text, transport=None):
    """平台直发一句纯文本到已认证的路由（私聊或群），返回有界回执。"""
    if not text or not str(text).strip():
        return {"state": "skipped", "reason": "blank_line"}
    platform_name = str(getattr(route, "platform_name", "") or "").lower()
    peer = str(getattr(route, "peer", "") or "")
    raw_onebot = peer.isdigit() and (not platform_name or platform_name in {"aiocqhttp", "qq", "onebot"})
    if not raw_onebot:
        # QQ Official / Telegram / other AstrBot-native platforms use the
        # platform session transport instead of a OneBot action.
        platform = getattr(route, "platform", None)
        if platform is None or not callable(getattr(platform, "send_by_session", None)):
            return {"state": "failed", "reason": "platform_transport_unavailable"}
        try:
            from astrbot.core.message.components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.core.platform.message_type import MessageType
            kind = MessageType.GROUP_MESSAGE if route.kind is ScopeKind.GROUP else MessageType.FRIEND_MESSAGE
            await platform.send_by_session(MessageSession(route.platform_id, kind, peer),
                                           MessageChain([Plain(str(text))]))
        except Exception:  # noqa: BLE001 - send outcome is unknown, never replayed here
            return {"state": "failed", "reason": "platform_call_failed"}
        receipt = digest(["text-notice-session.v1", route.platform_id, route.login, peer, str(text)])
        return {"state": "delivered", "message_id": "session:" + receipt, "receipt": receipt}
    api = transport if transport is not None else _transport(controller, route)
    if route.kind is ScopeKind.GROUP:
        action, target = "send_group_msg", {"group_id": int(route.peer)}
    else:
        action, target = "send_private_msg", {"user_id": int(route.peer)}
    try:
        result = await api.call_action(action, self_id=route.login, **target,
                                       message=[{"type": "text", "data": {"text": str(text)}}])
    except Exception:  # noqa: BLE001 - 平台调用失败只记状态，不重放
        return {"state": "failed", "reason": "platform_call_failed"}
    identifier = result.get("message_id") if isinstance(result, Mapping) else None
    return {"state": "delivered", "message_id": identifier,
            "receipt": digest(["text-notice.v1", route.platform_id, route.login, route.peer, str(text)])}


async def send_group_notice(controller, route, *, text, mention_id="", transport=None):
    """Send one bounded group relay through the authenticated account route."""
    if not text or not str(text).strip():
        return {"state": "skipped", "reason": "blank_line"}
    peer = getattr(route, "peer", None)
    if not isinstance(peer, str) or not peer.strip():
        return {"state": "failed", "reason": "group_route_invalid"}
    mention = str(mention_id or "").strip()
    if mention and not mention.isdigit():
        return {"state": "failed", "reason": "mention_target_invalid"}
    platform = getattr(route, "platform", None)
    platform_name = str(getattr(route, "platform_name", "") or "").lower()
    if mention and platform_name == "telegram":
        # Telegram renders an @ from the component's username; an id-only At is
        # silently dropped by the adapter.  A relay that promised a mention must
        # fail closed here instead of delivering without it.
        return {"state": "failed", "reason": "mention_target_unsupported"}
    raw_onebot = peer.isdigit() and (not platform_name or platform_name in {"aiocqhttp", "qq", "onebot"})

    if raw_onebot:
        api = transport if transport is not None else _transport(controller, route)
        message = [{"type": "text", "data": {"text": str(text)}}]
        if mention:
            message = [{"type": "at", "data": {"qq": mention}},
                       {"type": "text", "data": {"text": " "}}, *message]
        try:
            result = await api.call_action("send_group_msg", self_id=route.login,
                                           group_id=int(peer), message=message)
        except Exception:  # noqa: BLE001 - platform call failure is not replayed
            return {"state": "failed", "reason": "platform_call_failed"}
        identifier = result.get("message_id") if isinstance(result, Mapping) else None
        return {"state": "delivered", "message_id": identifier,
                "receipt": digest(["group-notice.v1", route.platform_id, route.login, peer,
                                   mention, str(text)])}

    # QQ Official and other AstrBot-native platforms use their own session
    # transport.  A normal return means the platform accepted the send; the
    # synthetic receipt is deterministic and is never used to replay.
    if platform is None or not callable(getattr(platform, "send_by_session", None)):
        return {"state": "failed", "reason": "group_transport_unavailable"}
    try:
        from astrbot.core.message.components import At, Plain
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.platform.message_session import MessageSession
        from astrbot.core.platform.message_type import MessageType
        chain = []
        if mention:
            chain.extend([At(qq=mention), Plain(" ")])
        chain.append(Plain(str(text)))
        session = MessageSession(route.platform_id, MessageType.GROUP_MESSAGE, peer)
        await platform.send_by_session(session, MessageChain(chain))
    except Exception:  # noqa: BLE001 - send outcome is unknown, never replayed here
        return {"state": "failed", "reason": "platform_call_failed"}
    return {"state": "delivered", "message_id": "session:" + digest(
        ["group-session-notice.v1", route.platform_id, route.login, peer, mention, str(text)]),
        "receipt": digest(["group-session-notice.v1", route.platform_id, route.login,
                           peer, mention, str(text)])}


async def send_private_notice(controller, event, *, text, route, transport=None):
    """Send one bounded private notice through the authenticated account route."""
    if not text or not str(text).strip():
        return {"state": "skipped", "reason": "blank_line"}
    peer = getattr(route, "peer", None)
    if not isinstance(peer, str) or not peer.strip():
        return {"state": "failed", "reason": "private_route_invalid"}
    platform_name = str(getattr(route, "platform_name", "") or "").lower()
    raw_onebot = peer.isdigit() and (not platform_name or platform_name in {"aiocqhttp", "qq", "onebot"})
    if raw_onebot:
        api = transport if transport is not None else _transport(controller, route)
        target = {"user_id": int(peer)}
        # QQ 群临时会话需要同时带来源群，否则协议端无法定位这轮临时会话。
        group_id = getattr(route, "group_id", "")
        if group_id:
            target["group_id"] = int(group_id)
        try:
            result = await api.call_action("send_private_msg", self_id=route.login,
                                          message=[{"type": "text", "data": {"text": str(text)}}],
                                          **target)
        except Exception:  # noqa: BLE001 - platform call failure is not replayed
            return {"state": "failed", "reason": "platform_call_failed"}
        identifier = result.get("message_id") if isinstance(result, Mapping) else None
        return {"state": "delivered", "message_id": identifier,
                "receipt": digest(["private-notice.v1", route.platform_id, route.login, peer, str(text)])}

    platform = getattr(route, "platform", None)
    if platform is None or not callable(getattr(platform, "send_by_session", None)):
        return {"state": "failed", "reason": "private_transport_unavailable"}
    try:
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.platform.message_session import MessageSession
        from astrbot.core.platform.message_type import MessageType
        await platform.send_by_session(MessageSession(route.platform_id, MessageType.FRIEND_MESSAGE, peer),
                                       MessageChain([Plain(str(text))]))
    except Exception:  # noqa: BLE001 - send outcome is unknown, never replayed here
        return {"state": "failed", "reason": "platform_call_failed"}
    receipt = digest(["private-session-notice.v1", route.platform_id, route.login, peer, str(text)])
    return {"state": "delivered", "message_id": "session:" + receipt, "receipt": receipt}


def prepare_relay_transport(controller, route):
    """Validate the pinned native route before an effectful dispatch barrier."""
    peer = getattr(route, "peer", None)
    if not isinstance(peer, str) or not peer.strip():
        raise TaskError("host_notice_route_invalid", "relay destination is empty")
    if getattr(route, "kind", None) is ScopeKind.PRIVATE and not peer.isdigit():
        raise TaskError("host_notice_route_invalid", "private destination must be a numeric account ID")
    platform_name = str(getattr(route, "platform_name", "") or "").lower()
    if platform_name not in {"", "aiocqhttp", "qq", "onebot"}:
        if getattr(route, "platform", None) is None:
            raise TaskError("host_notice_route_invalid", "native platform transport is unavailable")
        return None
    return _transport(controller, route)


def prepare_private_notice_transport(controller, route):
    """Backward-compatible private route validator."""
    return prepare_relay_transport(controller, route)


def _transport(controller, route):
    """Only the original reverse-WebSocket account of this route may send."""
    client = getattr(route, "client", None)
    api = getattr(getattr(client, "_api", None), "_wsr_api", None)
    clients = getattr(client, "_wsr_api_clients", None)
    if (api is None or type(api) is not controller.native_types.reverse_api
            or clients is None or not clients.get(route.login)):
        raise TaskError("host_notice_transport_changed",
                        "native reverse-WebSocket account is unavailable")
    return api


def private_notice_allowed(settings, *, group=False):
    """正式版：投递不再有"预览/授权"两道闸，因此这句话总是允许发出。

    保留这个函数是为了让"能不能发"只有一个判断点——将来若再加回某种总开关，
    改这里一处即可；调用方不需要知道现在的答案恒为真。
    """
    return True


async def notify_refusal(controller, session, event, *, context):
    """没有对话权限时按配置回一句；返回有界回执或 ``None``（没发就没发）。

    只对**已配置目标**有效：路由要能从已认证的事件里解出来。陌生号没有配置、
    也没有身份，走的是另一条通道（见 docs/PRIVATE_USER_ACCESS_20260930.md）。
    """
    from .recalls import authenticate_notice_route

    if context is None or not private_notice_allowed(controller.settings):
        return None
    application = session.application
    at = controller.now()
    text = application.private_notice.due(context, at)
    if not text:
        return None
    route = authenticate_notice_route(controller, event, ScopeKind.PRIVATE, expected_post="message")
    if route is None:
        return None
    receipt = await send_private_notice(controller, event, text=text, route=route)
    if receipt.get("state") == "delivered":
        application.private_notice.mark_sent(context, context.actor.subject.subject_id, text, at)
    return receipt
