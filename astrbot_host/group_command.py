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
    """Resolve the actual sender and require installation core membership."""
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


async def _set_group_image(controller, session, target, route, enable, at):
    """Turn every gate needed for group image requests on/off together.

    A single chat command must not leave the role photo switch, request policy,
    native image action switch or capability consent disagreeing.  Group image
    permission is stored on role image-output settings, so the installation
    default and every configured role are written together; a role override
    must not silently keep the old value.
    """
    from companion_v2.configuration.models import ConfigLayer, ConfigTarget
    from companion_v2.conversation.media.output_settings import (
        ImageOutputConfigurationOwner, configuration_fields,
    )
    from companion_v2.foundation.refs import SubjectKind, SubjectRef

    installation = session.runtime.installation_id
    owner = ImageOutputConfigurationOwner(session.runtime)
    fields = configuration_fields()
    role_ids = tuple(dict.fromkeys(
        str(getattr(persona, "role_id", "") or "")
        for persona in getattr(getattr(session, "settings", None), "personas", ())
    ))
    targets = [ConfigTarget(installation, ConfigLayer.INSTALLATION)]
    targets.extend(ConfigTarget(installation, ConfigLayer.ROLE,
                                SubjectRef(installation, SubjectKind.BOT, role_id),
                                bot_id=target.bot_id)
                   for role_id in role_ids if role_id)
    for config_target in targets:
        snapshot = owner.read(config_target, fields)
        current = {key.rsplit(".", 1)[-1]: item.value for key, item in snapshot.fields.items()}
        changes = {"conversation.image_output.group_enabled": bool(enable)}
        if enable:
            changes.update({
                "conversation.image_output.allow_user_requests": True,
                "conversation.image_output.request_permission": "everyone",
            })
            if current.get("trigger_mode") == "off":
                changes["conversation.image_output.trigger_mode"] = "rules"
        owner.write(config_target, fields, changes=changes, reset=(),
                    expected_revision=snapshot.revision, observed_at=at)

    native = getattr(session, "native_tool_port", None) or getattr(controller, "native_extensions", None)
    if native is not None and hasattr(native, "set_image_enabled"):
        from .native_extensions.image_settings import read_image_settings, write_image_settings
        path = getattr(native, "image_settings_path", None)
        settings = read_image_settings(path)
        if enable:
            settings = {**settings, "image.generate": True, "image.edit": True}
            write_image_settings(path, settings)
            native.set_image_enabled(settings)
            lease = native._lease("image") if hasattr(native, "_lease") else None
            if lease is not None and hasattr(native, "_ensure_installation"):
                native._ensure_installation(lease)
            if hasattr(native, "reconcile_service"):
                await native.reconcile_service("image")
    return enable


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
    if command.intent in ("group_image_enable", "group_image_disable"):
        if not _core_sender(session, route, target, at=at):
            text = "未执行：只有安装管理员可以更改本群生图权限。"
        else:
            enabled = command.intent == "group_image_enable"
            try:
                await _set_group_image(controller, session, target, route, enabled, at)
            except Exception as error:  # noqa: BLE001 - report a stable command failure
                code = getattr(error, "code", "group_image_command_failed")
                text = f"未执行：生图配置保存失败（{code}）。"
            else:
                text = ("已开启本群生图：角色生图、用户请求、群聊生图、全员权限和原生图片开关已一起打开。"
                        if enabled else
                        "已关闭本群生图：群成员不能再请求生图，私聊生图和主动生图不受影响。")
    elif command.intent == "group_llm_status":
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
