"""Actual private-message timing for interpreting queued conversational follow-ups."""

from datetime import datetime, timezone

from companion_v2.execution.models import require, stamp
from companion_v2.foundation.refs import ScopeKind

from .native_history import dialogue


INSTRUCTION = (
    "private_reply_context 按真实输入时间和原生发送回调整理私聊时序，记录有界，不证明对方已读。"
    "turn_id 是本次请求内的短引用，同一值才表示同一轮；text_in_host_history 表示正文已在宿主历史中，"
    "text_in_current_prompt 表示正文就是本轮原话，省略重复正文不代表消息未发送。"
    "理解本轮短话时，先看 before_input 中在最早一条合并输入到达前已发出的内容；同一 turn_id 的多个气泡属于同一次回复。"
    "before_input 只来自宿主原生历史；角色主动消息的投递回执可能只在 companion_background 的"
    "「本窗口已发送的主动消息与提醒」里。用户短句紧接着主动消息时，先核对该节的最近气泡，"
    "不要因为 before_input 没列出这几条就回退到更早的普通回复。"
    "last_sent_reply_turn_id 只是最近发送的时间锚点，具体承接对象仍结合原话、明确引用、否定和话题判断。"
    "after_input 中的消息是在本轮输入之后才发出的，不能把用户较早的应声或确认解释成对这些后来内容的回答。"
    "若 current_inputs 含多条合并输入，按各条 received_at 分别还原时序；前后列表以首条划界，"
    "两条输入之间发出的消息会出现在 after_input，可供后一条承接；不能用首条的时界代替各条输入的到达时间。"
    "宿主历史可能按处理顺序摆放完整回复，其中尚未发出的句子也不能假定用户已经看到。"
    "连续的应声、确认或补充可以都在衔接同一次较早的回复，保留那次请求的对象和进度，不逐条另起一件事。"
    "后来已发送的内容可用于了解处理进展、避免重复承诺或执行，不能反过来改写用户原意；"
    "用户明确转向、撤回、纠正或另指一条消息时按其本意接续，不把所有短话都当成同意。"
)
BEFORE_TURNS = 24
AFTER_TURNS = 12
# SegmentationPolicy accepts at most 64 bubbles in one reply turn.
MAX_BUBBLES_PER_TURN = 64


def _moment(value):
    try:
        at = datetime.fromisoformat(value) if isinstance(value, str) else value
        return at if isinstance(at, datetime) and at.tzinfo is not None else None
    except ValueError:
        return None


def merged_input_sequence(entries):
    """Keep each merged plain input's own arrival time without changing its identity."""
    result = []
    for entry in entries:
        if len(entry) >= 3:
            event, text, value = entry[:3]
        else:
            event, text = entry
            value = getattr(event, "created_at", None)
        try:
            at = datetime.fromtimestamp(value, timezone.utc) if type(value) in (int, float) else _moment(value)
        except (ValueError, OverflowError, OSError):
            at = None
        if at is not None and isinstance(text, str) and text.strip():
            result.append({"text": text, "received_at": stamp(at)})
    return sorted(result, key=lambda item: item["received_at"])


def capture_private_reply_context(messages, context, native_input, *, at, merged_inputs=()):
    """Read only authorized input observations and completed native send callbacks."""
    if getattr(getattr(context, "scope", None), "kind", None) is not ScopeKind.PRIVATE:
        return None
    received = _moment(getattr(native_input, "at", None))
    now = _moment(at)
    if received is None or now is None or received > now:
        return None
    require(messages.tasks.authorize_context(context, action="conversation.reply", observed_at=now))
    inputs = [{"text": native_input.text, "received_at": stamp(received)}]
    if merged_inputs:
        inputs = [{"text": item["text"], "received_at": stamp(moment)}
                  for item in merged_inputs
                  if isinstance(item, dict) and isinstance(item.get("text"), str)
                  and (moment := _moment(item.get("received_at"))) is not None
                  and moment <= now]
        inputs.sort(key=lambda item: item["received_at"])
        if not inputs:
            inputs = [{"text": native_input.text, "received_at": stamp(received)}]
        else:
            received = _moment(inputs[0]["received_at"])
    earlier = dialogue(messages, context, now, end=stamp(received),
                       limit=BEFORE_TURNS * MAX_BUBBLES_PER_TURN, max_chars=3600,
                       include_turns=True)
    later = dialogue(messages, context, now, start=stamp(received), end=stamp(now),
                     limit=AFTER_TURNS * MAX_BUBBLES_PER_TURN, max_chars=1800,
                     include_turns=True)

    def project(items, *, after_input=False, limit):
        result = []
        previous_speaker = None
        for item in items:
            if item["speaker"] == "user" and item["event_id"] == native_input.event_id:
                previous_speaker = None
                continue
            moment = _moment(item["at"])
            if moment is None or (moment > received) != after_input:
                previous_speaker = None
                continue
            if after_input and item["speaker"] != "assistant":
                previous_speaker = None
                continue
            entry = {name: item[name] for name in ("speaker", "text", "at", "turn_id")}
            if (entry["speaker"] == previous_speaker == "assistant" and entry["turn_id"]
                    and result[-1]["turn_id"] == entry["turn_id"]):
                result[-1]["text"] += "\n" + entry["text"]
                result[-1]["at"] = entry["at"]
            else:
                result.append(entry)
            previous_speaker = entry["speaker"]
        return result[-limit:]

    before = project(earlier, limit=BEFORE_TURNS)
    after = project(later, after_input=True, limit=AFTER_TURNS)
    last_reply = next((item["turn_id"] for item in reversed(before)
                       if item["speaker"] == "assistant"), None)
    return {"input_received_at": stamp(received), "captured_at": stamp(now),
            "current_inputs": inputs, "before_input": before, "after_input": after,
            "last_sent_reply_turn_id": last_reply, "history_is_bounded": True,
            "delivery_evidence": "native_send_return; recipient_read_unknown"}
