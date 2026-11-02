"""跨窗口递话的授权判定（纯逻辑，不碰发送）。

口径来自产品要求：

* 核心管理员可以让 bot 帮忙向白名单私聊或群聊递话；
  且需要一次确认（避免模型自作主张把内容发出去）；
* **其他用户**可以让 bot 给核心管理员递话——目标只能是核心管理员本人，
  不给他们发到任意窗口的能力；
* 群聊、未开启、空文本、超长文本一律拒绝。

这里只回答“这次递话该不该放行、要不要确认”，真正的发送由宿主层完成并写审计。
把判定独立出来，是为了让授权边界可以脱离网络与数据库被穷举测试。
"""

from dataclasses import dataclass

DENIED_DISABLED = "relay_disabled"
DENIED_MEMBER_DISABLED = "relay_member_disabled"
DENIED_MEMBER_SENDER = "relay_member_sender_not_allowed"
DENIED_TEXT_EMPTY = "relay_text_empty"
DENIED_TEXT_TOO_LONG = "relay_text_too_long"
DENIED_GROUP = "relay_group_not_allowed"
DENIED_TARGET = "relay_target_not_allowed"
DENIED_SELF = "relay_target_is_current_window"

ALLOWED = "relay_allowed"

MAX_TEXT_CHARS = 500


@dataclass(frozen=True)
class RelayRequest:
    """一次递话请求：发到哪、发什么。"""

    destination_kind: str          # "private" | "group"
    destination_id: str
    text: str
    source_id: str = ""            # 发起窗口，用于“不要发给自己”
    mention_id: str = ""           # 群聊中要 @ 的成员账号 ID
    relay_style: str = ""
    delay_until_seen: bool = False
    need_receipt: bool = False
    confirm_before_report: bool = False


@dataclass(frozen=True)
class RelayDecision:
    allowed: bool
    code: str
    reason: str = ""
    needs_confirm: bool = False    # True 表示必须由核心管理员确认后才发送
    audience: str = ""             # "owner" | "admin_target"，便于审计区分来源


def decide(request: RelayRequest, *, actor_is_core: bool, core_target_id: str,
           allowed_targets=(), allowed_groups=(), enabled: bool = True,
           require_confirm: bool = True,
           max_chars: int = MAX_TEXT_CHARS,
           member_relay_enabled: bool = True,
           member_relay_max_chars: int = MAX_TEXT_CHARS,
           member_relay_allowed_senders=(), actor_id: str = "") -> RelayDecision:
    """判定这次递话是否允许，以及要不要确认。

    ``enabled`` controls the core administrator's outbound relay.
    ``member_relay_enabled`` controls relay **to** the core administrator, which
    is a separate default-on capability with its own text and sender limits.
    """
    text = request.text if isinstance(request.text, str) else ""
    if not text.strip():
        return RelayDecision(False, DENIED_TEXT_EMPTY, "递话内容为空")
    kind = request.destination_kind if isinstance(request.destination_kind, str) else ""
    if kind not in {"private", "group"}:
        return RelayDecision(False, DENIED_TARGET, "递话目标类型无效")
    destination = request.destination_id if isinstance(request.destination_id, str) else ""
    if not destination:
        return RelayDecision(False, DENIED_TARGET, "递话目标为空")
    if request.source_id and destination == request.source_id:
        return RelayDecision(False, DENIED_SELF, "目标就是当前窗口，无需递话")

    if actor_is_core:
        if not enabled:
            return RelayDecision(False, DENIED_DISABLED, "当前窗口没有开启跨窗口递话")
        if len(text) > max(1, int(max_chars)):
            return RelayDecision(False, DENIED_TEXT_TOO_LONG, f"递话内容超过 {max_chars} 字上限")
        if kind == "group":
            if destination not in set(allowed_groups or ()):
                return RelayDecision(False, DENIED_GROUP,
                                     "核心管理员只能发到已加入白名单的群聊目标")
            return RelayDecision(True, ALLOWED, needs_confirm=bool(require_confirm),
                                 audience="admin_group")
        if destination not in set(allowed_targets or ()):
            return RelayDecision(False, DENIED_TARGET,
                                 "核心管理员只能发到已加入白名单的私聊目标")
        return RelayDecision(True, ALLOWED, needs_confirm=bool(require_confirm),
                             audience="admin_target")

    # 其他用户只能把话递给核心管理员的私聊窗口。这条路径不依赖发起窗口的
    # “允许递话”开关；它默认开启，但受独立开关、发送者名单和长度限制。
    if not member_relay_enabled:
        return RelayDecision(False, DENIED_MEMBER_DISABLED, "当前窗口未开启向核心管理员递话")
    senders = set(member_relay_allowed_senders or ())
    if senders and actor_id not in senders:
        return RelayDecision(False, DENIED_MEMBER_SENDER, "你不在可向核心管理员递话的名单中")
    if len(text) > max(1, int(member_relay_max_chars)):
        return RelayDecision(False, DENIED_TEXT_TOO_LONG,
                             f"递话内容超过 {member_relay_max_chars} 字上限")
    if kind != "private":
        return RelayDecision(False, DENIED_GROUP, "其他用户不能向群聊递话")
    if not core_target_id:
        return RelayDecision(False, DENIED_TARGET, "尚未配置核心管理员的私聊目标")
    if destination != core_target_id:
        return RelayDecision(False, DENIED_TARGET,
                             "其他用户只能给核心管理员递话")
    return RelayDecision(True, ALLOWED, needs_confirm=False, audience="owner")


__all__ = [
    "RelayRequest", "RelayDecision", "decide",
    "ALLOWED", "DENIED_DISABLED", "DENIED_MEMBER_DISABLED", "DENIED_MEMBER_SENDER",
    "DENIED_TEXT_EMPTY", "DENIED_TEXT_TOO_LONG",
    "DENIED_GROUP", "DENIED_TARGET", "DENIED_SELF", "MAX_TEXT_CHARS",
]
