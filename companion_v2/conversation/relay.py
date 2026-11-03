"""跨窗口递话的执行计划：把配置、发起者身份与判定拼成一条待发送记录。

拆分理由：`relay_policy` 只回答"能不能发"，这里负责"发什么、记什么"，
真正的网络发送留在宿主层（`astrbot_host/`）。这样授权与审计可以先被测试钉住，
发送实现晚一步接上也不会改变已经承诺的行为。

产品口径（2026-10-19）：

* 核心管理员可以让 bot 帮忙向白名单私聊或群聊递话；
* 其他用户只能让 bot 给核心管理员递话；
* 未开启、空文本、超长、目标不在白名单或发给自己一律拒绝。
"""

from dataclasses import dataclass, field

from .relay_policy import RelayDecision, RelayRequest, decide

DEFAULT_MAX_CHARS = 500


@dataclass(frozen=True)
class RelayPlan:
    """一条可以直接交给宿主发送的递话计划。"""

    decision: RelayDecision
    destination_kind: str = ""
    destination_id: str = ""
    text: str = ""
    mention_id: str = ""
    relay_style: str = "persona"
    delay_until_seen: bool = False
    need_receipt: bool = False
    confirm_before_report: bool = False
    audit: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def needs_confirm(self) -> bool:
        return self.decision.needs_confirm


def _text_setting(value, default):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def plan_relay(request: RelayRequest, settings, *, actor_is_core: bool,
               actor_id: str = "", source_label: str = "") -> RelayPlan:
    """给定窗口设置与发起者身份，产出递话计划（含审计字段）。

    ``settings`` 来自配置组 ``conversation.relay``；缺省即“未开启”，
    所以没有配置过的窗口不会意外获得跨窗口发送能力。
    """
    settings = settings if isinstance(settings, dict) else {}

    def targets(name):
        value = settings.get(name)
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.replace("[", "").replace("]", "")
                            .replace(chr(34), "").split(",") if item.strip())
        return tuple(value or ())

    decision = decide(
        request,
        actor_is_core=bool(actor_is_core),
        core_target_id=str(settings.get("core_target_id") or ""),
        allowed_targets=targets("allowed_targets"),
        allowed_groups=targets("allowed_groups"),
        enabled=settings.get("enabled") is True,
        require_confirm=settings.get("require_confirm") is not False,
        max_chars=_text_setting(settings.get("max_text_chars"), DEFAULT_MAX_CHARS),
        member_relay_enabled=settings.get("member_relay_enabled") is not False,
        member_relay_max_chars=_text_setting(
            settings.get("member_relay_max_text_chars"), DEFAULT_MAX_CHARS),
        member_relay_allowed_senders=targets("member_relay_allowed_senders"),
        actor_id=actor_id,
    )
    audit = {
        "kind": "conversation.relay",
        "actor": actor_id,
        "actor_is_core": bool(actor_is_core),
        "source": source_label or request.source_id,
        "destination": request.destination_id,
        "destination_kind": request.destination_kind,
        "mention": request.mention_id,
        "relay_style": request.relay_style or settings.get("default_style", "persona"),
        "delay_until_seen": request.delay_until_seen,
        "need_receipt": request.need_receipt,
        "confirm_before_report": request.confirm_before_report,
        "audience": decision.audience,
        "code": decision.code,
        "chars": len(request.text or ""),
        "needs_confirm": decision.needs_confirm,
    }
    if not decision.allowed:
        return RelayPlan(decision, audit=audit)
    return RelayPlan(decision, request.destination_kind, request.destination_id,
                     (request.text or "").strip(), request.mention_id,
                     request.relay_style or settings.get("default_style", "persona"),
                     request.delay_until_seen, request.need_receipt,
                     request.confirm_before_report, audit)


__all__ = ["RelayPlan", "plan_relay", "DEFAULT_MAX_CHARS"]
