"""Handle explicit phone commands in the authenticated private chat."""

import re

from companion_v2.device.checkout import APPROVAL_COMMAND, CANCEL_COMMAND, QUICK_APPROVAL
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind

from .binding import is_private_message, message_text
from .private_notice import send_private_notice
from .recalls import authenticate_notice_route


PATTERN = re.compile(rf"({APPROVAL_COMMAND}|{CANCEL_COMMAND})[ \t]+([0-9a-f]{{10}})")
SCREEN_COMMANDS = frozenset(("手机屏幕", "查看手机屏幕", "看看手机屏幕", "手机现在显示什么"))
SCREEN_FAILURES = {
    "phone_disabled": "请先在「手机使用」里启用 ADB 手机。",
    "phone_subject_denied": "当前私聊未获得手机读取权限。",
    "phone_busy": "手机正在执行另一个任务，结束后可以查看画面。",
}


async def receive_phone_command(controller, event, *, target):
    if not is_private_message(event):
        return None
    command = message_text(event).strip()
    screen_read = command.rstrip("。？?！!").strip() in SCREEN_COMMANDS
    match = PATTERN.fullmatch(command)
    if not screen_read and match is None and command not in (QUICK_APPROVAL, CANCEL_COMMAND):
        return None
    session = controller.session
    if session is None or not controller.claims(event):
        return Outcome.failed("phone_checkout_unavailable", "there is no active private route")
    route = None
    try:
        route = authenticate_notice_route(controller, event, ScopeKind.PRIVATE, expected_post="message")
        if route is None:
            return Outcome.failed("phone_checkout_unavailable", "there is no active private route")
        captured = require(await session.bridge.capture(event))
        if target is not None and hasattr(session, "authority"):
            if session.bridge.target_for_scope(captured.route.scope) != target:
                raise TaskError("phone_checkout_unavailable", "the event differs from its private target")
            context = session.authority.select(target, captured.route.scope, captured.role)
        else:
            context = session.profile.target
        if (captured.principal != context.actor.principal or captured.route.scope != context.scope
                or captured.recipients != context.audience or captured.role != context.role_binding.role
                or session.application.private_access_gate.denied(context, at=controller.now())):
            raise TaskError("phone_checkout_unavailable", "the event differs from the checkout requester")
        if captured.text.strip() != command:
            raise TaskError("phone_checkout_unavailable", "the command is not the original private message")
        service = session.application.phone_actions
        if screen_read:
            service.create_screen_read(context, "手机当前显示什么？", request_id=captured.event_id)
            return Outcome.success("phone-screen:queued")
        approve = command == QUICK_APPROVAL if match is None else match[1] == APPROVAL_COMMAND
        token = match[2] if match is not None else None
        service.answer_checkout(context, token, approve=approve, received_at=captured.at)
        text = "收到确认，我会先核对手机上的订单。" if approve else "已取消这次手机订单操作。"
    except TaskError as error:
        if not route:
            return Outcome.failed("phone_checkout_unavailable", "there is no active private route")
        if screen_read:
            text = SCREEN_FAILURES.get(error.code, "当前私聊暂时无法读取手机画面。")
        else:
            text = "当前私聊没有可确认的订单。请先让 Bot 核对结账页并发出订单回执。"
    receipt = await send_private_notice(controller, event, text=text, route=route)
    if receipt.get("state") != "delivered":
        return Outcome.failed("phone_checkout_reply_not_sent", "checkout status could not be delivered")
    return Outcome.success("phone-screen:unavailable" if screen_read else "phone-checkout:received")
