"""Group participation references derived from native conversation observations."""

from datetime import timedelta
from types import SimpleNamespace

from companion_v2.memory.group.access import owner_key
from companion_v2.execution.models import stamp
from .group_interjection import (ACKNOWLEDGED_STEP, IGNORED_STEP, INTERJECTION, REPEAT_FOLLOW, REPEAT_INTERRUPT,
                                 WEIGHT_MAX, WEIGHT_MIN, WEIGHT_START, addresses_role, day_key,
                                 interjection_state, openings)
from .group_observation import observe, parse_moment
from .participation import INSTRUCTION, group_participation_settings
from .participation_prompts import group_hints, interjection_instruction, observation_instruction
from .reply_settings import group_safety_settings, group_wakeup_settings


AUTONOMOUS_REASONS = frozenset({"interjection", "repeat", "interest", "context_word", "image_interest", "cold_group", "behavior"})


def _act(turn):
    decision = turn.get("participation") or {}
    if decision.get("mode") == "skip" or turn.get("mentioned"):
        return "", ""
    kind = decision.get("act")
    if kind in (INTERJECTION, REPEAT_FOLLOW, REPEAT_INTERRUPT):
        return kind, str(decision.get("signature") or "")
    if decision:
        return "", ""
    # Replies from before the native choose tool was restored still have a
    # genuine native send outcome and their original candidate classification.
    return (INTERJECTION, "") if turn.get("reason") in AUTONOMOUS_REASONS else ("", "")


def _speech_record(turns, visible, *, at, policy, timezone_name):
    acts = [(turn, *_act(turn)) for turn in turns if _act(turn)[0]]
    acts.sort(key=lambda item: item[0]["returned_at"])
    today = day_key(at, timezone_name)
    record = {"used_today": sum(day_key(parse_moment(turn["returned_at"]), timezone_name) == today
                                for turn, _, _ in acts),
              "last_act_at": acts[-1][0]["returned_at"] if acts else "", "weight": WEIGHT_START,
              "repeat": {"signature": "", "acts": 0, "last_act_at": ""}}
    for turn, kind, signature in acts:
        if kind in (REPEAT_FOLLOW, REPEAT_INTERRUPT) and signature:
            previous = record["repeat"]
            moment, last = parse_moment(turn["returned_at"]), parse_moment(previous["last_act_at"])
            same = signature == previous["signature"] and last is not None and (moment - last).total_seconds() <= 600
            record["repeat"] = {"signature": signature, "acts": previous["acts"] + 1 if same else 1,
                                "last_act_at": turn["returned_at"]}
        if policy.feedback_enabled:
            start = parse_moment(turn["returned_at"])
            end = min(at, start + timedelta(seconds=policy.feedback_window_seconds))
            seen = [item for item in visible if start < (parse_moment(item.get("received_at")) or start) <= end]
            acknowledged = any(addresses_role(item.get("text"), policy.address_words) for item in seen)
            if acknowledged or at >= start + timedelta(seconds=policy.feedback_window_seconds):
                step = ACKNOWLEDGED_STEP if acknowledged else -IGNORED_STEP
                record["weight"] = round(min(WEIGHT_MAX, max(WEIGHT_MIN, record["weight"] + step)), 4)
    return record


def speaker_history_instruction(items, current_speaker):
    """Render a short, account-attributed history so names cannot move between speakers."""
    lines = []
    for item in tuple(items or ())[-8:]:
        speaker = str(item.get("speaker_id") or "").strip()
        text = " ".join(str(item.get("text") or "").split())
        if not speaker or not text:
            continue
        lines.append(f"[speaker_id={speaker}] {text[:160]}")
    if not lines:
        return ""
    return ("\n群聊近期原文按账号归属，speaker_id 不同就是不同的人，不能把上一位的称呼、"
            "昵称、话头或情绪安到当前发言者身上。\n" + "\n".join(lines)
            + f"\n本轮 current_group_speaker={current_speaker}；只代表本轮，不代表上面每一个人。")


def native_participation_turn(participation, context, native_input, settings):
    """Read effective references; the model remains responsible for participation."""
    values = group_participation_settings(participation.runtime, context)
    at = native_input.at
    visible, turns = participation.admission.native_participation_history(
        context, native_input.instruction, at=at, max_age_hours=max(48, values["group_context_max_age_hours"]))
    merged_ids = set(getattr(native_input, "merged_event_ids", ()))
    merged_ids.add(native_input.event_id)
    visible = tuple(item for item in visible if item.get("event_id") not in merged_ids)
    recent_visible = tuple(item for item in visible
                          if (at - parse_moment(item["received_at"])).total_seconds() <= values["group_context_max_age_hours"] * 3600)
    current = {"kind": "member", "speaker_id": context.actor.principal.native_account_id,
               "received_at": stamp(at), "text": native_input.text, "event_id": native_input.event_id}
    visible = (*visible, current) if current not in visible else visible
    recent = [{"type": "group_member_message", **item} for item in recent_visible if item != current]
    recent.extend({"type": "native_group_reply", "turn_id": turn["event_id"], "returned_at": turn["returned_at"]}
                  for turn in turns)
    request = SimpleNamespace(event=SimpleNamespace(received_at=at), images=getattr(native_input, "images", ()),
                              context=SimpleNamespace(materials=()))
    hints = group_hints(request, context, settings, recent, {}, native=True,
                        current_text=native_input.text)
    if len(merged_ids) > 1:
        hints += ("\n本轮 current_source 已合并同一成员的连续纯文本补话；这些片段是一次表达，"
                  "不代表多次叫你。先按完整意思判断要不要接话；接话时抓重点一次回应，"
                  "不要按换行逐条追答。")
    observation = observe(participation.records, owner=owner_key(context), settings=values,
        items=(*recent_visible, current), at=at, current_speaker=context.actor.principal.native_account_id)
    hints += observation_instruction(values, observation["facts"], group_safety_settings(settings))
    policy = participation._interjection_policy(values, settings)
    record = _speech_record(turns, visible, at=at, policy=policy, timezone_name=participation.messages.timezone_name)
    mood = (observation["facts"].get("mood") or {}).get("top_mood", "")
    facts = interjection_state(policy, record, at=at,
        initiative=bool(native_input.text) and not addresses_role(native_input.text, policy.address_words),
        reasons=openings(native_input.text, mood_top=mood),
        interest_probability=group_wakeup_settings(settings)["group_wakeup_interest_probability"])
    hints += interjection_instruction(facts).replace("已确认", "宿主发送调用已返回的")
    if values["enable_group_repeat_follow"]:
        hints += ("\n群聊复读有独立的跟读和打断动作，由系统按管理员配置直接发送。"
                  "本轮是普通会话，按当前发言意图回应；不要借可见的复读内容扩展话题、解释其中的事实或自行启动复读动作。")
    hints += participation.behavior.instruction(context, native_input)
    act = INTERJECTION if facts["enabled"] and facts["initiative"] else ""
    return {"instruction": INSTRUCTION + hints
                + speaker_history_instruction(recent_visible, context.actor.principal.native_account_id)
                + "\n这里的发送记录只证明 AstrBot 发送调用正常返回，不等于平台确认送达；缺失记录不能证明此前无人发言。",
            "act": act, "signature": ""}
