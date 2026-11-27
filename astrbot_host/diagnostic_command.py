"""Fast-path deterministic handler for private '陪伴 答疑确认' / '陪伴 答疑取消'.

Executes directly on the control plane before entering the LLM/TTS pipeline:
- Bare command ('陪伴 答疑确认' / '陪伴 答疑取消'): lists currently pending repair
  proposals with their 4-char short code, changed fields, and remaining lifetime.
- With exact proposal ID or unambiguous >=4-char suffix: immediately confirms or
  cancels the proposal and sends a concise deterministic receipt in <1s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

from companion_v2.diagnostics.models import DiagnosticContext
from companion_v2.diagnostics.repairs import RepairProposalService
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind

from .binding import message_text
from .functional_commands import parse_functional_command
from .private_notice import send_private_notice
from .recalls import authenticate_notice_route


_CONFIRM_ALIASES = ("答疑确认", "应用答疑建议")
_CANCEL_ALIASES = ("答疑取消", "取消答疑建议")
_ROOTS = ("私聊陪伴", "主动陪伴", "陪伴")

_FAILURE_TEXT = {
    "repair_proposal_unavailable": "未找到对应的答疑修复建议（可能不存在或不属于当前会话）。可发送「陪伴 答疑确认」查看待审列表。",
    "repair_proposal_ambiguous": "匹配到多条同后缀的待审建议，请附更长后缀或完整编号后再试。",
    "repair_proposal_expired": "该修复建议已过期，请重新发送「陪伴 答疑 <问题>」生成最新建议。",
    "repair_proposal_closed": "该修复建议此前已完成确认或取消，不能重复变更。",
    "configuration_revision_conflict": "目标配置在生成建议后已变化，原建议已失效，请重新检查并生成新建议。",
    "diagnostic_evidence_changed": "相关运行证据在生成建议后已变化，原建议已失效，请重新检查并生成新建议。",
    "core_access_required": "当前账号缺少核心管理员权限，无法应用该配置修复。",
    "authorization_denied": "当前账号缺少所需配置权限，无法执行该操作。",
    "authorization_revoked": "当前账号的配置修改权限已被撤销，无法执行该操作。",
    "authorization_expired": "当前配置授权已过期，请重新授权后再试。",
    "authorization_not_active": "当前配置授权尚未生效，暂时无法执行该操作。",
    "binding_revoked": "当前身份授权已撤销，未执行。",
    "binding_rebound": "当前身份绑定已变化，未执行。",
    "role_binding_changed": "当前角色绑定已变化，原建议已失效，请重新检查。",
}


@dataclass(frozen=True)
class DiagnosticControlCommand:
    action: str  # "confirm" | "cancel"
    argument: str = ""


def _extract_action_from_text(text: str) -> str | None:
    stripped = text.strip().lstrip("/")
    for root in _ROOTS:
        if stripped.startswith(root):
            rest = stripped[len(root):].strip()
            for alias in _CONFIRM_ALIASES:
                if rest == alias or rest.startswith(alias + " "):
                    return "confirm"
            for alias in _CANCEL_ALIASES:
                if rest == alias or rest.startswith(alias + " "):
                    return "cancel"
    return None


def parse_diagnostic_command(text: object) -> DiagnosticControlCommand | None:
    command = parse_functional_command(text, channel="private")
    if command is None:
        return None
    if command.intent == "diagnostics_confirm":
        return DiagnosticControlCommand("confirm", command.argument)
    if command.intent == "diagnostics_cancel":
        return DiagnosticControlCommand("cancel", command.argument)
    if command.intent == "manage_diagnostics" and isinstance(text, str):
        action = _extract_action_from_text(text)
        if action is not None:
            return DiagnosticControlCommand(action, command.argument)
    return None


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    if value is None:
        return "空"
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _summarize_delta(review: dict) -> str:
    delta = review.get("delta") if isinstance(review, dict) else None
    if not isinstance(delta, dict) or not delta:
        return "配置调整"
    parts = []
    for key, change in list(delta.items())[:4]:
        before = _format_value((change.get("before") or {}).get("value"))
        after = _format_value((change.get("after") or {}).get("value"))
        short_key = key.rsplit(".", 1)[-1]
        parts.append(f"{short_key}: {before} → {after}")
    if len(delta) > 4:
        parts.append(f"等共 {len(delta)} 项")
    return "；".join(parts)


def _remaining_text(review: dict, now: datetime) -> str:
    expires_raw = review.get("expires_at") if isinstance(review, dict) else None
    if not isinstance(expires_raw, str):
        return ""
    try:
        remaining = int((datetime.fromisoformat(expires_raw) - now).total_seconds())
    except ValueError:
        return ""
    if remaining <= 0:
        return "即将过期"
    minutes = max(1, (remaining + 59) // 60)
    return f"约 {minutes} 分钟后过期"


def _failure_text(code: str, fallback: str = "") -> str:
    return "未执行：" + _FAILURE_TEXT.get(code, fallback or f"操作未完成（{code}）。")


def _display_codes(items) -> dict[str, str]:
    """Assign each listed proposal a concise, collision-free suffix for copy/paste."""
    ids = [item.proposal_id for item in items]
    result = {}
    for proposal_id in ids:
        chosen = proposal_id
        for width in (4, 8, 12, 16):
            candidate = proposal_id[-width:] if len(proposal_id) >= width else proposal_id
            needle = candidate.lower()
            if sum(1 for other in ids if other.lower().endswith(needle)) == 1:
                chosen = candidate
                break
        result[proposal_id] = chosen
    return result


def _execute(command: DiagnosticControlCommand, context, application) -> str:
    diagnostics = getattr(getattr(application, "tools", None), "diagnostics", None)
    if diagnostics is None:
        return "未执行：当前诊断服务尚未就绪。"
    original = DiagnosticContext(context.actor, context.role_binding, context.audience)
    repairs = RepairProposalService(diagnostics)
    argument = command.argument.strip()

    if not argument:
        listed = repairs.list_pending(original, limit=5)
        if listed.failure is not None:
            return _failure_text(listed.failure.code, listed.failure.reason)
        items = listed.unwrap()
        if not items:
            return "当前没有待确认的答疑修复建议。如需排查配置，请先发送「陪伴 答疑 <问题>」。"
        now = diagnostics.now()
        display_codes = _display_codes(items)
        lines = [f"当前有 {len(items)} 条待确认的答疑修复建议："]
        for index, item in enumerate(items, start=1):
            review = json.loads(item.review_json)
            code = display_codes[item.proposal_id]
            summary = _summarize_delta(review)
            remaining = _remaining_text(review, now)
            rationale = str(review.get("rationale") or "").strip()[:60]
            tail = f"（{remaining}）" if remaining else ""
            lines.append(f"{index}. [{code}] {summary}{tail}")
            if rationale:
                lines.append(f"   说明：{rationale}")
        if len(items) == 1:
            code = display_codes[items[0].proposal_id]
            lines.append(f"回复「陪伴 答疑确认 {code}」立即应用，或「陪伴 答疑取消 {code}」取消。")
        else:
            lines.append("回复「陪伴 答疑确认 <短码>」应用对应建议，或「陪伴 答疑取消 <短码>」取消。")
        return "\n".join(lines)

    if len(argument.split()) != 1:
        return "未执行：请只附一个建议编号或其后 4 位短码，或直接发送「陪伴 答疑确认」查看待审列表。"

    resolved = repairs.resolve_proposal_id(original, argument)
    if resolved.failure is not None:
        return _failure_text(resolved.failure.code, resolved.failure.reason)
    proposal_id = resolved.unwrap()
    current = repairs.read(original, proposal_id=proposal_id)
    if current.failure is not None:
        return _failure_text(current.failure.code, current.failure.reason)
    proposal = current.unwrap()
    code = repairs.short_code(proposal.proposal_id)
    review = json.loads(proposal.review_json)

    if command.action == "confirm":
        # Delegate straight to repairs.confirm() so _finish() enforces the exact
        # authoritative order: write permission -> expiry -> config revision -> evidence.
        finished = repairs.confirm(
            original,
            proposal_id=proposal.proposal_id,
            expected_revision=proposal.revision,
            review_token=proposal.review_token,
        )
        if finished.failure is not None:
            return _failure_text(finished.failure.code, finished.failure.reason)
        result = finished.unwrap()
        summary = _summarize_delta(review)
        note = "（已沿用原确认回执）" if result.replayed else ""
        return f"已确认并保存配置修改（短码 {code}）：{summary}。{note}"

    finished = repairs.cancel(
        original,
        proposal_id=proposal.proposal_id,
        expected_revision=proposal.revision,
        review_token=proposal.review_token,
    )
    if finished.failure is not None:
        return _failure_text(finished.failure.code, finished.failure.reason)
    result = finished.unwrap()
    note = "（该建议此前已取消）" if result.replayed else ""
    return f"已取消修复建议（短码 {code}），配置未作任何改动。{note}"


async def receive_diagnostic_command(controller, event, *, target=None):
    """Return None when this is not a private diagnostic confirm/cancel command."""
    command = parse_diagnostic_command(message_text(event))
    if command is None:
        return None
    prepare = getattr(controller, "prepare_core_reply_context", None)
    if not callable(prepare) or controller.session is None:
        return None
    route = None
    try:
        route = authenticate_notice_route(controller, event, ScopeKind.PRIVATE, expected_post="message")
        if route is None:
            return None
        plan = await prepare(event, target)
        if plan.failure is not None:
            return None
        context = plan.unwrap().context
        if context.scope.kind is not ScopeKind.PRIVATE:
            return None
        text = _execute(command, context, controller.session.application)
    except TaskError as error:
        if route is None:
            return None
        text = _failure_text(error.code, str(error))
    receipt = await send_private_notice(controller, event, text=text, route=route)
    if receipt.get("state") != "delivered":
        return Outcome.failed("diagnostic_command_reply_not_sent",
                              "the diagnostic confirmation receipt could not be delivered")
    return Outcome.success({"handled": True, "action": command.action, "text": text})
