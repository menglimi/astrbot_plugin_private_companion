"""Concise, message-complete logs for passive wake and debounce decisions.

The wake reason, the whole message and the timing numbers are the three things
an operator needs to explain why a reply waited.  Keep each fact on one line:
newlines in the native message are escaped, and the byte/character budget is
bounded so one very long message cannot flood the host log.
"""

from logging import getLogger


logger = getLogger(__name__)
MAX_MESSAGE_CHARS = 8000
CHANNEL_LABELS = {"private": "私聊", "group": "群聊"}
REASON_LABELS = {
    "mention": "被@",
    "mention_all": "@全体",
    "native_wakeup": "宿主唤醒词",
    "direct_word": "唤醒词/名称",
    "wake_word_with_other_mention": "@ 他人时命中唤醒词",
    "quoted_reply": "引用回复",
    "native_quote": "引用机器人",
    "active_follow_up": "补充正在处理的任务",
    "reply_follow_up": "接续刚发出的回复",
    "image_interest": "图片唤醒",
    "question": "问题/求助",
    "context_word": "相关词",
    "interest": "兴趣词",
    "repeat": "复读",
    "interjection": "自主插话",
    "cold_group": "冷群接话",
    "role_choice": "人格自主判断",
    "behavior": "互动习惯",
    "private_message": "私聊消息",
}


def reason_label(reason):
    """Turn a native admission code into the label an operator can read."""
    text = str(reason or "").strip()
    return REASON_LABELS.get(text, text or "未标注")


def timing(**values):
    """Render only the timing values that are actually known."""
    parts = []
    for name, value in values.items():
        if value is None:
            continue
        try:
            parts.append(f"{name}={int(round(float(value)))}ms")
        except (TypeError, ValueError):
            continue
    return " ".join(parts) or "-"


def _message(text):
    value = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(value) <= MAX_MESSAGE_CHARS:
        return value
    return value[:MAX_MESSAGE_CHARS] + f"…<+{len(value) - MAX_MESSAGE_CHARS} chars>"


def wake(channel, reason, text, *, target="", timing_text=""):
    """One line when a passive message wins admission and starts a window."""
    label = CHANNEL_LABELS.get(channel, channel)
    suffix = f"（{timing_text}）" if timing_text else ""
    logger.info(
        "[CompanionV2] 收到唤醒消息 channel=%s reason=%s target=%s message=%s；"
        "开始等待用户后续消息%s",
        label, reason_label(reason), target or "-", _message(text), suffix)


def window(channel, *, target="", text="", timing_text=""):
    """The resolved quiet/deadline values once the native policy is known."""
    label = CHANNEL_LABELS.get(channel, channel)
    logger.info(
        "[CompanionV2] 防抖时间 channel=%s target=%s message=%s；%s",
        label, target or "-", _message(text), timing_text or "-")


def followup(channel, text, *, target="", count=0, elapsed_ms=None, timing_text=""):
    """One line for each message that joins the still-open window."""
    label = CHANNEL_LABELS.get(channel, channel)
    timing_value = f" elapsed_ms={int(round(elapsed_ms))}" if elapsed_ms is not None else ""
    suffix = f"（{timing_text}）" if timing_text else ""
    logger.info(
        "[CompanionV2] 防抖期间收到后续消息 channel=%s target=%s count=%d%s "
        "message=%s；继续等待用户后续消息%s",
        label, target or "-", count, timing_value, _message(text), suffix)


def settled(channel, *, target="", inputs=0, followups=0, waited_ms=None, timing_text=""):
    """One line when the quiet window closes and the turn may reply."""
    label = CHANNEL_LABELS.get(channel, channel)
    timing_value = f" waited_ms={int(round(waited_ms))}" if waited_ms is not None else ""
    if followups:
        outcome = f"收到 {followups} 条后续消息，开始正常回复"
    else:
        outcome = "未接收到后续消息，开始正常回复"
    suffix = f"（{timing_text}）" if timing_text else ""
    logger.info(
        "[CompanionV2] 防抖结束 channel=%s target=%s inputs=%d followups=%d%s；%s%s",
        label, target or "-", inputs, followups, timing_value, outcome, suffix)


def cancelled(channel, *, target="", reason="cancelled"):
    """One line when a window is abandoned before it can produce a turn."""
    label = CHANNEL_LABELS.get(channel, channel)
    logger.info("[CompanionV2] 防抖取消 channel=%s target=%s reason=%s",
                label, target or "-", reason)
