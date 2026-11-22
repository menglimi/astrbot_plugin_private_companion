"""Bounded desktop screen analysis summary for one authorized private reply.

The source is the V2 native Screen repository, already scoped to one exact
private window and gated by role enablement plus per-window consent.  This
module projects at most one concise, redacted summary within a 15-minute window
so the companion can answer follow-up questions in private QQ chat without ever
storing raw images or leaking into group chats or other users.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from companion_v2.sources.privacy import redact_text


SNAPSHOT_VERSION = "conversation.screen.v1"
MAX_AGE_SECONDS = 15 * 60
MAX_SUMMARY_CHARS = 240
MAX_LABEL_CHARS = 60

SCREEN_CONTEXT_INSTRUCTION = (
    "screen_context 是当前私聊主人本人授权共享桌面后、15 分钟内最近一次脱敏屏幕解读摘要，"
    "仅表示当时画面大致呈现的内容，不代表此刻屏幕未切换，也不包含原始截图。"
    "只在对方问起刚给你看过的屏幕内容或当前话题自然相关时使用；不要主动播报正在看对方屏幕，"
    "也不要凭空补造摘要里没写出的代码细节、聊天记录或隐私内容。"
    "群聊和其他用户的窗口绝不能使用或提及这份私聊材料。"
)


def _clean(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = redact_text(value)
    text = " ".join(text.split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def project_screen_context(interpretation, *, captured_at: datetime, observed_at: datetime):
    """Project one bounded, redacted screen summary within 15 minutes; None if stale/empty."""
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("screen context requires a timezone-aware observation time")
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("screen context requires a timezone-aware capture time")
    age_seconds = max(0, int((observed_at - captured_at).total_seconds()))
    if age_seconds > MAX_AGE_SECONDS:
        return None
    payload = interpretation
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return None
        try:
            decoded = json.loads(stripped)
        except ValueError:
            decoded = None
        payload = decoded if isinstance(decoded, dict) else {"summary": stripped}
    if not isinstance(payload, dict):
        return None
    summary = _clean(
        payload.get("summary") or payload.get("description") or payload.get("text") or "",
        MAX_SUMMARY_CHARS,
    )
    if not summary:
        return None
    scene = _clean(payload.get("scene") or "", MAX_LABEL_CHARS)
    resource_label = _clean(payload.get("resource_label") or "", MAX_LABEL_CHARS)
    result = {
        "version": SNAPSHOT_VERSION,
        "basis": "user_authorized_screen_analysis",
        "privacy": "private_only",
        "source": "desktop_screen_analysis",
        "summary": summary,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "age_seconds": age_seconds,
    }
    if scene:
        result["scene"] = scene
    if resource_label:
        result["resource_label"] = resource_label
    return result


__all__ = [
    "MAX_AGE_SECONDS",
    "MAX_LABEL_CHARS",
    "MAX_SUMMARY_CHARS",
    "SCREEN_CONTEXT_INSTRUCTION",
    "SNAPSHOT_VERSION",
    "project_screen_context",
]
