"""跨窗口递话的宿主侧发送：按 plan_relay 的结论决定发不发、发什么。

学 V1 的做法：V1 的 atrelay 就是拿目标私聊 id 直接调 OneBot 的
`send_private_msg`。V2 已经有同样的原语——`private_notice.send_private_notice`
（OneBot 账号走 `call_action("send_private_msg", ...)`，QQ Official / Telegram
等 AstrBot 原生平台走 `platform.send_by_session`），所以这里不重造发送通道，
只补三件 V1 没有严格做好的事：

1. **先判定再发送**：一律经过 `plan_relay`，未通过就只返回拒绝码，不触网；
2. **管理员路径必须带确认**：`needs_confirm=True` 且没有 `confirmed=True` 时
   只回一条待确认结果，绝不发送；
3. **审计**：把计划里的 audit 字段原样返回，由调用方写入 outbox。

Telegram 的 @ 对象需要用户名、且没有 OneBot 群成员列表可解析，因此带 @ 的群聊
递话在 `relay_tools` 解析阶段就以 ``host_relay_target_unsupported`` 拒绝；
``send_group_notice`` 在最后一道边界上对 telegram + mention 也会失败关闭，
不会静默丢掉 @ 后照常发送。

这个模块只做"一次递话"，不订阅事件、不注册工具——接入点由调用方决定。
"""

from companion_v2.conversation.relay import plan_relay
from companion_v2.conversation.relay_policy import RelayRequest

from .private_notice import send_group_notice, send_private_notice


class RelayNotDelivered(Exception):
    """递话没有被送出去（未通过判定、待确认或发送失败）。"""

    def __init__(self, code, message, *, audit=None, needs_confirm=False):
        super().__init__(message)
        self.code = code
        self.audit = audit or {}
        self.needs_confirm = needs_confirm


def with_destination(route, destination_id, *, kind=None):
    """Keep the authenticated account/client while replacing the peer route."""
    from dataclasses import replace
    try:
        return replace(route, peer=str(destination_id), kind=kind) if kind is not None else replace(route, peer=str(destination_id))
    except (TypeError, ValueError):
        from types import SimpleNamespace
        values = dict(vars(route)) if hasattr(route, "__dict__") else {}
        values["peer"] = str(destination_id)
        if kind is not None:
            values["kind"] = kind
        return SimpleNamespace(**values)


async def relay_private(controller, event, *, request: RelayRequest, settings,
                        actor_is_core: bool, actor_id: str = "", confirmed: bool = False,
                        route=None, base_route=None, transport=None):
    """把一条私聊递话发出去，或返回"还需要确认"。

    ``route`` 由调用方用现有的路由鉴权（`recalls.authenticate_notice_route`
    那套）解析好传进来；本函数不自己猜目标，避免把内容发到未授权窗口。
    """
    plan = plan_relay(request, settings, actor_is_core=actor_is_core, actor_id=actor_id)
    if not plan.allowed:
        raise RelayNotDelivered(plan.decision.code, plan.decision.reason, audit=plan.audit)
    if plan.needs_confirm and not confirmed:
        # 管理员的递话要先确认：这里不发，只把待确认的事实交回调用方。
        raise RelayNotDelivered("relay_confirmation_required",
                                "这段递话需要先确认，确认后才会发送。",
                                audit=plan.audit, needs_confirm=True)
    if route is None and base_route is not None:
        # 同一个 Bot 账号、只换对方 id：复用已认证事件里的 platform/login/client，
        # 不让递话自己去找账号或客户端（那等于凭空扩大发送能力）。
        route = with_destination(base_route, plan.destination_id)
    if route is None:
        raise RelayNotDelivered("relay_target_unavailable", "目标私聊不可用，未发送。",
                                audit=plan.audit)
    receipt = await send_private_notice(controller, event, text=plan.text, route=route, transport=transport)
    if receipt.get("state") != "delivered" or receipt.get("message_id") is None:
        return {"state": "unknown", "text": plan.text, "audit": plan.audit, "receipt": receipt}
    return {"state": "sent", "text": plan.text, "audit": plan.audit, "receipt": receipt}


async def relay_group(controller, event, *, request: RelayRequest, settings,
                      actor_is_core: bool, actor_id: str = "", confirmed: bool = False,
                      route=None, base_route=None, transport=None):
    """Send one group relay after the same policy/confirmation boundary."""
    plan = plan_relay(request, settings, actor_is_core=actor_is_core, actor_id=actor_id)
    if not plan.allowed:
        raise RelayNotDelivered(plan.decision.code, plan.decision.reason, audit=plan.audit)
    if plan.needs_confirm and not confirmed:
        raise RelayNotDelivered("relay_confirmation_required",
                                "这段递话需要先确认，确认后才会发送。",
                                audit=plan.audit, needs_confirm=True)
    if route is None and base_route is not None:
        route = with_destination(base_route, plan.destination_id, kind=plan.destination_kind)
    if route is None:
        raise RelayNotDelivered("relay_target_unavailable", "目标群聊不可用，未发送。",
                                audit=plan.audit)
    receipt = await send_group_notice(controller, route, text=plan.text,
                                      mention_id=plan.mention_id, transport=transport)
    if receipt.get("state") != "delivered" or receipt.get("message_id") is None:
        return {"state": "unknown", "text": plan.text, "audit": plan.audit, "receipt": receipt}
    return {"state": "sent", "text": plan.text, "audit": plan.audit, "receipt": receipt}


__all__ = ["RelayNotDelivered", "relay_group", "relay_private", "with_destination"]
