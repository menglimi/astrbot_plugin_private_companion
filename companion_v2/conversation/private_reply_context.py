"""Actual private-message timing for interpreting queued conversational follow-ups."""

from datetime import datetime, timezone
from difflib import SequenceMatcher
import logging
import re

from companion_v2.execution.models import require, stamp
from companion_v2.foundation.refs import ScopeKind

from .native_history import dialogue


logger = logging.getLogger(__name__)

INSTRUCTION = (
    "private_reply_context 按真实输入时间、原生发送回调与主动消息投递回执整理私聊时序，记录有界，不证明对方已读。"
    "turn_id 是本次请求内的短引用，同一值才表示同一轮；text_in_host_history 表示正文已在宿主历史中，"
    "text_in_current_prompt 表示正文就是本轮原话，省略重复正文不代表消息未发送。"
    "理解本轮短话时，先看 before_input 中在最早一条合并输入到达前已发出的内容；同一 turn_id 的多个气泡属于同一次回复。"
    "有 sent_ref 的助手气泡来自主动消息投递回执，part.sequence 是该次主动联系内的气泡顺序。"
    "背景对应条目标有 text_in_private_reply_context 时，可按 ref 和 part.sequence 找到时序里的原话。"
    "时序未列出的主动消息或提醒也可在 companion_background 的「本窗口已发送的主动消息与提醒」中核对。"
    "last_sent_reply_turn_id 只是最近已发送内容的时间锚点，具体承接对象仍结合原话、明确引用、否定和话题判断。"
    "after_input 中的消息是在本轮输入之后才发出的，不能把用户较早的应声或确认解释成对这些后来内容的回答。"
    "若 current_inputs 含多条合并输入，按各条 received_at 分别还原时序；前后列表以首条划界，"
    "两条输入之间发出的消息会出现在 after_input，可供后一条承接；不能用首条的时界代替各条输入的到达时间。"
    "宿主历史可能按处理顺序摆放完整回复，其中尚未发出的句子也不能假定用户已经看到。"
    "连续的应声、确认或补充可以都在衔接同一次较早的回复，保留那次请求的对象和进度，不逐条另起一件事。"
    "后来已发送的内容可用于了解处理进展、避免重复承诺或执行，不能反过来改写用户原意；"
    "用户明确转向、撤回、纠正或另指一条消息时按其本意接续，不把所有短话都当成同意。"
    "标有 repeat_suppressed 的助手条目表示该轮与较早助手回复高度重复，已省略重复正文以避免复读；"
    "请直接推进当前话题，不要照抄或复述此前已说过的大段内容。"
)
BEFORE_TURNS = 24
AFTER_TURNS = 12
# SegmentationPolicy accepts at most 64 bubbles in one reply turn.
MAX_BUBBLES_PER_TURN = 64
# Read-side repetition guard thresholds (sources are never mutated or deduplicated).
INTRA_TURN_DUPLICATE_MIN_CHARS = 16
REPEAT_SUPPRESS_MIN_CHARS = 24
REPEAT_SUPPRESS_SIMILARITY = 0.85
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_CHARS = "。！？!?…~～、，,.;；:： "


def _normalize_repeat_text(text):
    if not isinstance(text, str):
        return ""
    return _WHITESPACE_RE.sub("", text).strip(_TRAILING_PUNCT_CHARS)


def _is_substantial_repeat(first, second):
    if len(first) < REPEAT_SUPPRESS_MIN_CHARS or len(second) < REPEAT_SUPPRESS_MIN_CHARS:
        return False
    if first == second:
        return True
    shorter, longer = (len(first), len(second)) if len(first) <= len(second) else (len(second), len(first))
    if shorter / longer < REPEAT_SUPPRESS_SIMILARITY:
        return False
    return SequenceMatcher(None, first, second).ratio() >= REPEAT_SUPPRESS_SIMILARITY


def _slice_recent_turns(entries, limit):
    """Keep the most recent ``limit`` logical turns rather than raw bubble rows."""
    if limit <= 0 or not entries:
        return []
    kept = []
    turns_seen = 0
    last_turn_key = None
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        turn_id = entry.get("turn_id")
        turn_key = (entry.get("speaker"), turn_id) if turn_id else ("row", index)
        if turn_key != last_turn_key:
            if turns_seen >= limit:
                break
            turns_seen += 1
            last_turn_key = turn_key
        kept.append(entry)
    kept.reverse()
    return kept


def _suppress_repeated_assistant_turns(entries):
    """Suppress repeated substantial assistant turns on read without mutating sources.

    Keeps the first occurrence verbatim and marks later near-duplicate turns with
    ``repeat_suppressed=True`` while preserving their real ``at``, ``speaker``,
    and ``turn_id`` so timeline anchors stay intact and positive-feedback loops
    are broken.
    """
    seen = []
    suppressed = 0
    for entry in entries:
        if entry.get("speaker") != "assistant" or "sent_ref" in entry:
            continue
        text = entry.get("text")
        normalized = _normalize_repeat_text(text)
        if len(normalized) < REPEAT_SUPPRESS_MIN_CHARS:
            continue
        if any(_is_substantial_repeat(previous, normalized) for previous in seen):
            entry.pop("text", None)
            entry["repeat_suppressed"] = True
            suppressed += 1
        else:
            seen.append(normalized)
    return suppressed


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


def capture_private_reply_context(messages, context, native_input, *, at, merged_inputs=(), confirmed_contacts=()):
    """Merge authorized delivery material with input observations and native send callbacks."""
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

    sent_before, sent_after = [], []
    for contact in confirmed_contacts:
        sent_at = _moment(contact["delivered_at"])
        if sent_at is None or sent_at > now or not contact["text"].strip():
            continue
        ref = contact["ref"]
        entry = {"speaker": "assistant", "text": contact["text"], "at": stamp(sent_at),
                 "turn_id": "contact:" + ref, "sent_ref": ref, "part": contact["part"]}
        (sent_before if sent_at <= received else sent_after).append(entry)
    if sent_before:
        earlier = sorted((*earlier, *sent_before), key=lambda item: item["at"])
    if sent_after:
        later = sorted((*later, *sent_after), key=lambda item: item["at"])

    def project(items, *, after_input=False, limit):
        result = []
        turn_bubbles = []
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
            if "sent_ref" in item:
                entry.update(sent_ref=item["sent_ref"], part=item["part"])
                result.append(entry)
                turn_bubbles.append(None)
            elif (entry["speaker"] == previous_speaker == "assistant" and entry["turn_id"]
                    and result[-1]["turn_id"] == entry["turn_id"]):
                incoming_stripped = entry["text"].strip()
                seen_bubbles = turn_bubbles[-1]
                if (len(incoming_stripped) >= INTRA_TURN_DUPLICATE_MIN_CHARS
                        and seen_bubbles is not None and incoming_stripped in seen_bubbles):
                    result[-1]["at"] = entry["at"]
                else:
                    result[-1]["text"] += "\n" + entry["text"]
                    result[-1]["at"] = entry["at"]
                    if seen_bubbles is not None and incoming_stripped:
                        seen_bubbles.add(incoming_stripped)
            else:
                result.append(entry)
                stripped = entry["text"].strip()
                turn_bubbles.append({stripped} if stripped else set())
            previous_speaker = entry["speaker"]
        return _slice_recent_turns(result, limit)

    before = project(earlier, limit=BEFORE_TURNS)
    after = project(later, after_input=True, limit=AFTER_TURNS)
    suppressed_turns = _suppress_repeated_assistant_turns(before)
    if suppressed_turns:
        logger.warning(
            "[CompanionV2] private_reply_context suppressed repeated assistant turns count=%d",
            suppressed_turns,
        )
    last_reply = next((item["turn_id"] for item in reversed(before)
                       if item["speaker"] == "assistant"), None)
    payload = {"input_received_at": stamp(received), "captured_at": stamp(now),
               "current_inputs": inputs, "before_input": before, "after_input": after,
               "last_sent_reply_turn_id": last_reply, "history_is_bounded": True,
               "delivery_evidence": ("native_send_return; confirmed_contact_receipt; recipient_read_unknown"
                                     if sent_before or sent_after else "native_send_return; recipient_read_unknown")}
    if suppressed_turns:
        payload["repeat_suppression"] = {"detected": True, "suppressed_turns": suppressed_turns}
    return payload
