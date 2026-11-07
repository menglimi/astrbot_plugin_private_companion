"""Chat-side temporary block of model replies in one group.

V1 的「陪伴群 关闭 LLM」在 V2 没有对应状态：现有 `group_wakeup_enabled` 只关
非 @ 的自主候选，@、引用和唤醒词仍会回复。这条指令因此在模型之前直接处理：
校验原路由与安装管理员，写一条群级运行时状态，再用平台直发回执，不进入模型。
"""

from __future__ import annotations

from companion_v2.conversation.group_llm_block import (
    GroupLLMBlock,
    group_block_key,
    minutes_since,
)
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind

from .binding import message_text
from .functional_commands import GROUP_CONTROL_INTENTS, parse_functional_command
from .private_notice import send_text_notice
from .recalls import authenticate_notice_route


def parse_group_control(text):
    command = parse_functional_command(text, channel="group")
    return command if command is not None and command.intent in GROUP_CONTROL_INTENTS else None


def _core_sender(session, route, target, *, at):
    """Resolve the actual sender and require installation core membership.

    Platform admin flags are deliberately not trusted here: Telegram never sets
    ``event.role``/``is_admin`` for bot admins, and the same command must mean
    the same thing on every adapter.  The only authority is the installation's
    own CORE subject tier, resolved from the authenticated route sender; an
    unbound or non-core sender gets the readable refusal text instead of a
    crash.  A core administrator therefore has to be bound in the dashboard
    (their private participant id is what the group sender resolves to).
    """
    from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
    from .command_authority import is_core_subject
    from .multi_authority import HOST_ID

    principal = AuthenticatedHostPrincipal(
        session.runtime.installation_id, HOST_ID, route.platform_id, route.sender,
        target.bot_id, route.login)
    resolved = session.authority.identities.resolve_authenticated(principal, observed_at=at)
    if not resolved.is_success:
        return False
    return is_core_subject(session.runtime, resolved.unwrap().subject)


def _status_text(current):
    if current is None:
        return "本群 LLM 回复正常，当前没有临时封锁。"
    return (f"本群 LLM 回复处于临时封锁：开始于 {current.get('started_at')}，"
            f"操作者 {current.get('operator') or '未知'}。发送「陪伴群 开启LLM」恢复。")


async def receive_group_llm_command(controller, event, *, target=None):
    """Return None when this is not a handled group LLM command."""
    command = parse_group_control(message_text(event))
    if command is None:
        return None
    route = authenticate_notice_route(controller, event, ScopeKind.GROUP, expected_post="message")
    if route is None:
        return None
    session = controller.session
    if session is None or session.runtime is None:
        return None
    if target is None:
        target = controller.settings.group_target(route.platform_id, route.login, route.peer)
    if target is None or getattr(target, "group_id", None) != route.peer:
        return None
    key = group_block_key(platform_id=target.platform_id, account_id=target.account_id,
                          bot_id=target.bot_id, group_id=target.group_id)
    block = GroupLLMBlock(session.runtime, now=controller.now)
    at = controller.now()
    if command.intent == "group_llm_status":
        text = _status_text(block.status(key))
    else:
        if not _core_sender(session, route, target, at=at):
            text = "未执行：只有安装管理员可以更改本群 LLM 开关。"
        elif command.intent == "group_llm_block":
            value = block.block(key, operator=route.sender)
            text = (f"本群 LLM 回复已经处于临时封锁（开始于 {value.get('started_at')}），本次没有重复更改。"
                    if value.get("replayed") else
                    f"已临时封锁本群 LLM 回复（操作者 {route.sender}）。发送「陪伴群 开启LLM」恢复。")
        else:
            released = block.release(key, operator=route.sender)
            if released is None:
                text = "本群 LLM 回复本来就是正常的，没有可恢复的封锁。"
            else:
                elapsed = minutes_since(released.get("started_at"), at)
                span = f"封锁持续 {elapsed} 分钟" if elapsed is not None else "封锁已结束"
                text = f"已恢复本群 LLM 回复（{span}）。"
    receipt = await send_text_notice(controller, route, text=text)
    if receipt.get("state") != "delivered":
        return Outcome.failed("group_llm_command_reply_not_sent",
                              "the group command receipt could not be delivered")
    return Outcome.success({"handled": True, "intent": command.intent, "text": text})
