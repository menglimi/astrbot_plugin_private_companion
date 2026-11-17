"""Strict chat vocabulary for explicit calendar generation requests."""

from dataclasses import dataclass

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind

from .binding import is_private_message, message_text
from .private_notice import send_private_notice
from .recalls import authenticate_notice_route


_COMMANDS = {
    "生成日程": ("daily", False),
    "刷新日程": ("daily", True),
    "重新生成日程": ("daily", True),
    "生成周日程": ("horizon", False),
    "刷新周日程": ("horizon", True),
    "重新生成周日程": ("horizon", True),
}


@dataclass(frozen=True)
class CalendarCommand:
    mode: str
    refresh: bool


def parse_calendar_command(text):
    if type(text) is not str:
        return None
    words = text.strip().split()
    if words and words[0].startswith("/"):
        words[0] = words[0][1:]
    if words[:1] == ["陪伴"]:
        words = words[1:]
    if len(words) != 1:
        return None
    request = _COMMANDS.get(words[0])
    return CalendarCommand(*request) if request else None


async def receive_calendar_command(controller, event, session, *, target=None):
    if not is_private_message(event):
        return None
    command = parse_calendar_command(message_text(event))
    if command is None:
        return None
    try:
        route = authenticate_notice_route(controller, event, ScopeKind.PRIVATE, expected_post="message")
    except TaskError as error:
        return Outcome.failed(error.code, str(error))
    if route is None:
        return Outcome.failed("host_calendar_command_route", "calendar command needs its configured private route")
    try:
        if target is None:
            report = await session.plan_calendar(event, mode=command.mode, refresh=command.refresh)
        else:
            report = await session.plan_calendar(event, target, mode=command.mode, refresh=command.refresh)
        text = calendar_result_text(report)
    except TaskError as error:
        text = calendar_error_text(error.code)
    except (ValueError, TypeError, KeyError, AttributeError):
        text = "日程生成暂时无法完成，请检查陪伴小屋中的日程配置后重试。"
    receipt = await send_private_notice(controller, event, text=text, route=route)
    if receipt.get("state") != "delivered":
        return Outcome.failed("calendar_command_reply_not_sent",
                              "calendar command result could not be delivered")
    return Outcome.success("calendar-command:delivered")


def calendar_result_text(report):
    if report.get("state") == "succeeded":
        saved = report.get("saved", 0)
        rejected = report.get("rejected", ())
        replaced = report.get("replaced", 0)
        horizon = report.get("mode") == "horizon"
        text = ("未来日程已生成，共写入 %d 项。" if horizon else "今天的日程已生成，共写入 %d 项。") % saved
        if replaced:
            text += "已替换 %d 项旧的自动日程。" % replaced
        if rejected:
            text += "另有 %d 项未能写入。" % len(rejected)
        return text
    if report.get("state") == "already_attempted":
        return "这条日程生成请求已经处理过，没有重复写入。"
    code = report.get("code") or report.get("state") or "planning_incomplete"
    return calendar_error_text(code)


def calendar_error_text(code):
    messages = {
        "calendar_disabled": "角色日程总开关已关闭，请在「陪伴小屋」的日程与规划设置中开启后重试。",
        "planning_disabled": "自动规划已关闭，请在「陪伴小屋」的日程与规划设置中开启后重试。",
        "planning_mode_inactive": "当前使用连续日程模式；请切换到「日程编排」后再生成计划。",
        "calendar_access_denied": "当前角色没有日历写入权限，日程没有更改。",
        "calendar_write_denied": "当前角色没有日历写入权限，日程没有更改。",
        "scope_not_granted": "当前角色没有日历写入权限，日程没有更改。",
        "identity_capability_denied": "当前角色没有日历写入权限，日程没有更改。",
        "model_task_unregistered": "后台模型任务 calendar.plan 尚未配置，日程没有更改。",
        "planning_settings_changed": "生成期间日程设置发生变化，请重新发送命令。",
        "planning_calendar_changed": "生成期间日历内容发生变化，新计划没有写入；请重新发送命令。",
        "planning_role_changed": "当前角色绑定已变化，请确认角色后重新发送命令。",
        "private_access_dialogue_denied": "当前私聊没有陪伴对话权限。",
    }
    return messages.get(code, "日程生成未完成（%s），请检查日程、授权和后台模型配置。" % code)
