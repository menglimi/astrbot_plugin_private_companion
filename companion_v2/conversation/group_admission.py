"""Model-free group candidates and lightweight AstrBot core reply records."""

from collections import deque
import json

from companion_v2.execution.models import TaskError, digest, stamp
from .group_observation import parse_moment
from .group_timing import clean_group_text, repeat_burst, repeat_signature
from .group_questions import question_signal
from .group_followup import FOLLOW_UP_REASONS, progress as followup_progress
from .reply_settings import group_continuation_settings, group_wakeup_settings
from .reset import batch_current, boundary


FOLLOW_UP_SECONDS = 120
MAX_GROUPS = 128
MAX_OBSERVATIONS = 128
PREFIX = "conversation.group_admission.v1:"
CORE_PREFIX = "conversation.group_core_reply.v1:"
REASONS = {"mention": "明确 @ 机器人", "native_wakeup": "命中宿主唤醒词",
           "quoted_reply": "引用机器人发送记录中的群消息", "active_follow_up": "当前成员补充正在处理的任务",
           "native_quote": "宿主识别到引用机器人消息", "mention_all": "宿主配置允许 @ 全体唤醒",
           "reply_follow_up": "群成员可能接续机器人刚发出的回复", "direct_word": "命中配置的直接唤醒词或名称",
           "image_interest": "已开启图片唤醒", "question": "命中群聊问题或求助策略",
           "context_word": "命中配置的群聊相关词", "interest": "命中配置的兴趣词",
           "repeat": "本群复读达到配置阈值", "interjection": "命中已开启的自主插话线索",
           "cold_group": "命中配置的冷群接话策略", "role_choice": "原人格自主判断参与",
           "behavior": "与本角色学到的群聊互动习惯相关"}


class GroupReplyAdmission:
    def __init__(self, messages):
        self.messages, self.runtime = messages, messages.runtime
        self.observations = {}
        self._live_core_events = {}

    def _key(self, context):
        scope = context.scope
        return (scope.installation_id, scope.platform, scope.bot_id, scope.account_id, scope.native_scope_id,
                scope.session_id, context.role_binding.role, getattr(context.role_binding, "revision", 0))

    def reset_window(self, window):
        route = (window.installation_id, window.platform, window.bot_id, window.account_id,
                 window.native_scope_id, window.session_id)
        for key in tuple(self.observations):
            if key[:6] == route:
                del self.observations[key]
        for key in tuple(self._live_core_events):
            if key[:6] == route:
                del self._live_core_events[key]

    def note_live_core_turn(self, context, event_id, *, active):
        if not isinstance(event_id, str) or not event_id:
            return
        key = self._key(context)
        live = self._live_core_events.get(key)
        if active:
            if live is None:
                if len(self._live_core_events) >= MAX_GROUPS:
                    self._live_core_events.pop(next(iter(self._live_core_events)))
                live = []
                self._live_core_events[key] = live
            if event_id not in live:
                live.append(event_id)
                if len(live) > MAX_OBSERVATIONS:
                    del live[:-MAX_OBSERVATIONS]
        elif live is not None:
            if event_id in live:
                live.remove(event_id)
            if not live:
                self._live_core_events.pop(key, None)

    def has_concurrent_or_newer_core_turn(self, context, event_id):
        live = self._live_core_events.get(self._key(context), ())
        return any(other != event_id for other in live)

    def _sample(self, context, reason, probability, event_id, at, text):
        # Repeat observations of one event must keep the same decision. Include
        # the authorized window/role so independent bots do not share a draw.
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        key = [context.key, reason, event_id or [stamp(at), text]]
        return int(digest(key)[:16], 16) / (1 << 64) < probability

    def observe(self, context, *, text, speaker_id, received_at, event_id=None):
        reset = boundary(self.runtime, context.scope)
        if reset is not None and stamp(received_at) <= reset["reset_at"]:
            return
        key = self._key(context)
        if key not in self.observations:
            if len(self.observations) >= MAX_GROUPS:
                self.observations.pop(next(iter(self.observations)))
            self.observations[key] = deque(maxlen=MAX_OBSERVATIONS)
        item = {"kind": "member", "speaker_id": speaker_id,
                "received_at": stamp(received_at), "text": clean_group_text(text)}
        if event_id is not None:
            item["event_id"] = event_id
        if item not in self.observations[key]:
            self.observations[key].append(item)

    def candidate(self, context, *, text, instruction, at, settings, participation,
                  mentioned=False, native_wakeup=False, mentions_others=False, reply_ids=(),
                  mentions_all=False, quoted_bot=False, has_image=False, persona_name="", event_id=None,
                  continuation=None):
        self.messages.group_context.authorize(context, self.messages.tasks.now())
        wakeup = group_wakeup_settings(settings)
        if mentioned:
            return "mention"
        if self._quoted_reply(context, reply_ids, instruction, at):
            return "quoted_reply"
        if quoted_bot:
            return "native_quote"
        if native_wakeup:
            return "mention_all" if mentions_all else "native_wakeup"
        compact = clean_group_text(text)
        direct = list(wakeup["group_wakeup_direct_words"])
        if wakeup["group_wakeup_bot_name_enabled"]:
            direct.append(wakeup["group_wakeup_bot_name"] or persona_name)
        if context.actor.principal.native_account_id in wakeup["group_wakeup_primary_member_ids"]:
            direct.extend(wakeup["group_wakeup_owner_direct_words"])
        if (wakeup["group_wakeup_enabled"] and not mentions_others
                and any(word and word.casefold() in compact.casefold() for word in direct)):
            return "direct_word"
        continuation = continuation if continuation is not None else self.continuation_status(
            context, instruction, at, settings=settings, event_id=event_id)
        if continuation["limit_reached"]:
            return ""
        if mentions_others:
            return "behavior" if self._behavior_candidate(context, compact, wakeup, participation) else ""
        if continuation["candidate_reason"]:
            return continuation["candidate_reason"]
        if not wakeup["group_wakeup_enabled"]:
            return ""
        if (wakeup["group_wakeup_image_enabled"] and has_image
                and self._sample(context, "image_interest", wakeup["group_wakeup_image_weight"],
                                 event_id, at, compact)):
            return "image_interest"
        if not compact or not any(char.isalnum() for char in compact):
            return ""
        items = list(self.observations.get(self._key(context), ()))
        current = {"kind": "member", "speaker_id": context.actor.principal.native_account_id,
                   "received_at": stamp(at), "text": compact}
        if event_id is not None:
            current["event_id"] = event_id
        if current not in items:
            items.append(current)
        if participation["enable_group_repeat_follow"]:
            burst = repeat_burst(repeat_signature(compact), items, at=at,
                                 window_seconds=participation["group_repeat_window_seconds"])
            count = (burst["distinct"] if participation["group_repeat_count_distinct_users_only"]
                     else burst["count"]) if burst else 0
            if count >= participation["group_repeat_trigger_threshold"]:
                return "repeat"
        if self._behavior_candidate(context, compact, wakeup, participation):
            return "behavior"
        # A repeated question or interest keyword is still a literal repeat.
        # Other candidates remain references for the ordinary role reply.
        question = (self.question_reference(context, text=compact, instruction=instruction, at=at,
                                            settings=settings, event_id=event_id)
                    if wakeup["group_wakeup_question_enabled"] else {"score": 0})
        if (wakeup["group_wakeup_question_enabled"] and question["score"] > 0
                and question["score"] >= wakeup["group_wakeup_question_threshold"]):
            return "question"
        if any(word.casefold() in compact.casefold() for word in wakeup["group_wakeup_context_words"]):
            return "context_word"
        if (any(word.casefold() in compact.casefold() for word in wakeup["group_wakeup_interest_keywords"])
                and self._sample(context, "interest", wakeup["group_wakeup_interest_probability"],
                                 event_id, at, compact)):
            return "interest"
        if participation["enable_group_interjection"]:
            from .group_interjection import openings
            signals = openings(compact)
            # Broad open_question cues cannot reopen disabled/below-threshold
            # public questions through the independent interjection switch.
            if "refusal" not in signals and "joke_reaction" in signals:
                return "interjection"
        if wakeup["group_wakeup_cold_group_enabled"]:
            previous = [parse_moment(item["received_at"]) for item in items if item != current]
            previous = [when for when in previous if when is not None and when < at]
            if previous and (at - max(previous)).total_seconds() >= wakeup["group_wakeup_cold_group_idle_minutes"] * 60:
                return "cold_group"
        return ""

    def _behavior_candidate(self, context, text, wakeup, participation):
        if not wakeup["group_wakeup_enabled"] or not participation.get("enable_group_behavior_imitation", False):
            return False
        from .behavior import GroupBehavior
        return GroupBehavior(self.messages).candidate(context, text=text)

    def question_reference(self, context, *, text, instruction, at, settings, event_id=None):
        wakeup = group_wakeup_settings(settings)
        recent = [item for item in self.observations.get(self._key(context), ())
                  if not event_id or item.get("event_id") != event_id]
        return question_signal(text, recent=recent, at=at,
            speaker_id=context.actor.principal.native_account_id,
            bot_turns=self._current_core_turns(context, instruction, at),
            cooldown_seconds=wakeup["group_wakeup_cooldown_seconds"])

    def record(self, context, batch_id, *, reason, mentioned):
        key = PREFIX + batch_id
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
            saved = json.loads(row["value"]) if row else {"context_key": context.key, "reasons": [], "mentioned": False}
            if saved["context_key"] != context.key:
                raise TaskError("group_admission_changed", "reply admission belongs to another group turn")
            saved["mentioned"] = saved["mentioned"] or mentioned
            if reason in REASONS and reason not in saved["reasons"]:
                saved["reasons"].append(reason)
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, json.dumps(saved, ensure_ascii=False, sort_keys=True)))

    def recorded(self, context, batch_id):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (PREFIX + batch_id,))
        saved = json.loads(row["value"]) if row else None
        return saved if saved is not None and saved.get("context_key") == context.key else None

    def mark_logged(self, context, batch_id, stage):
        with self.runtime.transaction() as connection:
            saved = self.recorded(context, batch_id)
            if saved is None:
                return True
            stages = saved.setdefault("logged_stages", [])
            if stage in stages:
                return False
            stages.append(stage)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                               (json.dumps(saved, ensure_ascii=False, sort_keys=True), PREFIX + batch_id))
            return True

    def _core_key(self, context):
        return CORE_PREFIX + digest(self._key(context))

    def _core_turns(self, context):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (self._core_key(context),))
        if row is None:
            return []
        try:
            saved = json.loads(row["value"])
            return saved["turns"] if saved.get("version") == 1 and isinstance(saved.get("turns"), list) else []
        except (TypeError, ValueError, AttributeError):
            return []

    def _save_core_turns(self, connection, context, turns):
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (self._core_key(context), json.dumps({"version": 1, "turns": turns[-MAX_OBSERVATIONS:]},
                                               ensure_ascii=False, sort_keys=True)))

    def record_core_reply(self, context, event_id, *, instruction, reason, mentioned, at, continuation=None):
        """Remember a core turn without opening a V2 batch or delivery task."""
        with self.runtime.transaction() as connection:
            turns = self._core_turns(context)
            saved = next((turn for turn in turns if turn.get("event_id") == event_id), None)
            if saved is not None:
                if (saved.get("context_key") != context.key
                        or saved.get("instruction_digest") != digest(instruction)):
                    raise TaskError("group_admission_changed", "core reply admission belongs to another group turn")
                return saved
            saved = {"event_id": event_id, "context_key": context.key, "scope_key": context.scope.key,
                     "instruction_digest": digest(instruction),
                     "speaker_id": context.actor.principal.native_account_id,
                     "admitted_at": stamp(at), "state": "admitted", "send_state": "not_started",
                     "send_attempts": 0, "send_returns": 0, "unknown_sends": 0, "send_failed": 0,
                     "platform_ids": [], "reason": reason, "mentioned": bool(mentioned), "logged_stages": []}
            if continuation is not None:
                saved["followup_seconds"] = continuation["seconds"]
                saved["followup_base_turns"] = continuation["used_turns"]
            turns.append(saved)
            self._save_core_turns(connection, context, turns)
            return saved

    def recorded_core_reply(self, context, event_id):
        return next((turn for turn in self._core_turns(context)
                     if turn.get("event_id") == event_id and turn.get("context_key") == context.key), None)

    def mark_core_reply(self, context, event_id, *, stage, at, platform_ids=()):
        """Record observed host calls, never platform confirmation or retry eligibility."""
        with self.runtime.transaction() as connection:
            turns = self._core_turns(context)
            saved = next((turn for turn in turns if turn.get("event_id") == event_id
                          and turn.get("context_key") == context.key), None)
            if saved is None:
                return None
            if stage == "inflight" and saved["state"] == "admitted":
                saved.update(state="inflight", inflight_at=stamp(at))
            elif stage == "send_started":
                saved.update(state="inflight", inflight_at=stamp(at))
                saved["send_attempts"] += 1
                if saved["send_state"] not in ("unknown", "denied"):
                    saved["send_state"] = "started"
            elif stage == "send_returned":
                if saved["send_returns"] == 0:
                    saved["first_returned_at"] = stamp(at)
                    previous = followup_progress([turn for turn in self._current_core_turns(
                        context, None, at, instruction_digest=saved["instruction_digest"]) if turn["event_id"] != event_id], at=at,
                                                 seconds=saved.get("followup_seconds", FOLLOW_UP_SECONDS))
                    saved["followup_turns"] = (max(previous["used_turns"], saved.get("followup_base_turns", 0)) + 1
                                               if saved.get("reason") in FOLLOW_UP_REASONS else 0)
                saved["send_returns"] += 1
                saved["returned_at"] = stamp(at)
                if saved["send_state"] not in ("unknown", "denied"):
                    saved["send_state"] = "returned"
                saved["platform_ids"] = list(dict.fromkeys((*saved["platform_ids"], *platform_ids)))[-MAX_OBSERVATIONS:]
            elif stage == "send_unknown":
                saved["unknown_sends"] += 1
                saved["send_state"] = "unknown"
            elif stage == "send_denied":
                saved["send_state"] = "denied"
            elif stage == "send_failed":
                saved["send_failed"] = saved.get("send_failed", 0) + 1
                if saved["send_state"] not in ("unknown", "denied"):
                    saved["send_state"] = "failed"
            elif stage == "completed" and saved["state"] != "completed":
                saved.update(state="completed", completed_at=stamp(at))
            self._save_core_turns(connection, context, turns)
            return saved

    def mark_core_reply_logged(self, context, event_id, stage):
        with self.runtime.transaction() as connection:
            turns = self._core_turns(context)
            saved = next((turn for turn in turns if turn.get("event_id") == event_id
                          and turn.get("context_key") == context.key), None)
            if saved is None or stage in saved["logged_stages"]:
                return False
            saved["logged_stages"].append(stage)
            self._save_core_turns(connection, context, turns)
            return True

    def _current_core_turns(self, context, instruction, at, *, instruction_digest=None):
        reset = boundary(self.runtime, context.scope)
        return [turn for turn in self._core_turns(context)
                if turn.get("scope_key") == context.scope.key
                and turn.get("instruction_digest") == (instruction_digest or digest(instruction))
                and (when := parse_moment(turn.get("admitted_at"))) is not None and when <= at
                and (reset is None or stamp(when) > reset["reset_at"])]

    def native_participation_history(self, context, instruction, *, at, max_age_hours=12):
        """Return this authorized window's bounded observations and native sends."""
        self.messages.group_context.authorize(context, self.messages.tasks.now())
        reset = boundary(self.runtime, context.scope)
        items = [dict(item) for item in self.observations.get(self._key(context), ())
                 if (when := parse_moment(item.get("received_at"))) is not None
                 and 0 <= (at - when).total_seconds() <= max_age_hours * 3600
                 and (reset is None or stamp(when) > reset["reset_at"])]
        turns = [dict(turn) for turn in self._current_core_turns(context, instruction, at)
                 if (when := parse_moment(turn.get("returned_at"))) is not None
                 and 0 <= (at - when).total_seconds() <= max_age_hours * 3600
                 and turn.get("send_state") == "returned" and turn.get("send_returns", 0) > 0]
        return tuple(items), tuple(turns)

    def mark_core_participation(self, context, event_id, *, mode, reason, act="", signature="", source="", decision_action=""):
        """Record the role's actual native choice without a legacy reply batch."""
        with self.runtime.transaction() as connection:
            turns = self._core_turns(context)
            saved = next((turn for turn in turns if turn.get("event_id") == event_id
                          and turn.get("context_key") == context.key), None)
            if saved is not None:
                saved["participation"] = {"mode": mode, "reason": reason, "act": act, "signature": signature}
                if source:
                    saved["participation"]["source"] = source
                if decision_action:
                    saved["participation"]["decision_action"] = decision_action
                self._save_core_turns(connection, context, turns)

    def _intervening_message(self, context, after, at, event_id):
        current_speaker = context.actor.principal.native_account_id
        for item in self.observations.get(self._key(context), ()):
            when = parse_moment(item.get("received_at"))
            if when is None or not after < when <= at:
                continue
            if event_id is not None and item.get("event_id") == event_id:
                continue
            if event_id is None and when == at:
                continue
            if item.get("speaker_id") == current_speaker:
                continue
            return True
        return False

    def _core_continuation(self, context, turn, at, *, event_id=None, seconds=FOLLOW_UP_SECONDS):
        returned = turn.get("state") == "completed" and turn.get("send_state") == "returned"
        if (turn.get("scope_key") != context.scope.key
                or (not returned and turn.get("context_key") != context.key)
                or turn.get("reason") == "repeat"
                or turn.get("send_state") in ("unknown", "denied", "failed")):
            return ""
        choice = turn.get("participation") or {}
        if (turn.get("state") == "completed" and turn.get("send_state") == "not_started"
                and choice.get("source") == "laya" and choice.get("decision_action") == "wait"):
            when = parse_moment(turn.get("admitted_at"))
            if when is not None and any(item["speaker_id"] != turn["speaker_id"]
                    and when < (parse_moment(item["received_at"]) or when) <= at
                    for item in self.observations.get(self._key(context), ())):
                return ""
            return "active_follow_up" if when is not None and 0 <= (at - when).total_seconds() <= seconds else ""
        if turn.get("state") in ("admitted", "inflight"):
            when = parse_moment(turn.get("inflight_at") or turn.get("admitted_at"))
            if when is not None and any(item["speaker_id"] != turn["speaker_id"]
                    and when < (parse_moment(item["received_at"]) or when) <= at
                    for item in self.observations.get(self._key(context), ())):
                return ""
            return "active_follow_up" if when is not None and 0 <= (at - when).total_seconds() <= seconds else ""
        when = parse_moment(turn.get("returned_at"))
        if (turn.get("state") != "completed" or turn.get("send_state") != "returned"
                or when is None or not 0 <= (at - when).total_seconds() <= seconds):
            return ""
        # The first reply after a bot send can come from any group member.
        # Keep that member's short burst together, but stop when another
        # member speaks before the current event.
        if self._intervening_message(context, when, at, event_id):
            return ""
        return "reply_follow_up"

    @staticmethod
    def _is_silent_skip_turn(turn, speaker_id):
        if (turn.get("speaker_id") != speaker_id
                or turn.get("state") != "completed"
                or turn.get("send_state") != "not_started"
                or turn.get("send_attempts", 0) != 0):
            return False
        choice = turn.get("participation") or {}
        if choice.get("mode") != "skip":
            return False
        return not (choice.get("source") == "laya" and choice.get("decision_action") == "wait")

    def continuation_status(self, context, instruction, at, *, settings, event_id=None):
        policy = group_continuation_settings(settings)
        seconds = policy["group_conversation_followup_seconds"]
        maximum = policy["group_conversation_followup_max_turns"]
        enabled = policy["group_conversation_followup_enabled"]
        turns = self._current_core_turns(context, instruction, at)
        legacy = self._legacy_followup_turns(context, instruction, at)
        turns = [turn for turn in (*legacy, *turns) if turn.get("event_id") != event_id]
        current = followup_progress(turns, at=at, seconds=seconds)
        limited = enabled and current["used_turns"] + current["pending_turns"] >= maximum
        reason = self._continuation(context, instruction, at, event_id=event_id, seconds=seconds) if enabled else ""
        if (reason and current["anchor_at"] is not None and not current["active"]
                and turns and turns[-1].get("reason") in FOLLOW_UP_REASONS):
            reason = ""
        return {**current, "enabled": enabled, "seconds": seconds, "max_turns": maximum,
                "limit_reached": limited, "candidate_reason": "" if limited else reason}

    def _legacy_followup_turns(self, context, instruction, at):
        rows = self.runtime.read_all("""SELECT b.batch_id, b.created_at, a.value AS admission,
            MAX(p.delivered_at) AS returned_at
            FROM conversation_flows f JOIN conversation_batches b ON b.batch_id = f.batch_id
            JOIN conversation_parts p ON p.batch_id = b.batch_id AND p.state = 'delivered'
            JOIN conversation_sent_history h ON h.part_id = p.part_id AND h.batch_id = p.batch_id
                AND h.receipt_ref = p.receipt_ref
            JOIN conversation_delivery_receipts r ON r.part_id = p.part_id AND r.receipt_ref = p.receipt_ref
            LEFT JOIN runtime_metadata a ON a.key = ? || b.batch_id
            WHERE f.route_key = ? AND b.role_instruction = ? AND b.state NOT IN ('cancelled', 'invalidated')
                AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
                AND json_extract(b.context_json, '$.role_binding.revision') = ?
            GROUP BY b.batch_id ORDER BY f.sequence DESC LIMIT 128""",
            (PREFIX, context.scope.key, instruction, context.role_binding.role.subject_id, context.role_binding.revision))
        turns = []
        for row in reversed(rows):
            created = parse_moment(row["created_at"])
            if (created is None or created > at
                    or not batch_current(self.runtime, row["batch_id"], context.scope)):
                continue
            try:
                saved = json.loads(row["admission"]) if row["admission"] else {}
                reasons = saved.get("reasons", []) if isinstance(saved, dict) else []
                if not isinstance(reasons, list):
                    reasons = []
            except (TypeError, ValueError):
                reasons = []
            reason = ("reply_follow_up" if reasons and all(item in FOLLOW_UP_REASONS for item in reasons)
                      else "role_choice")
            turns.append({"event_id": row["batch_id"], "admitted_at": created.isoformat(),
                          "returned_at": row["returned_at"], "send_state": "returned", "send_returns": 1,
                          "reason": reason})
        return turns

    def _continuation(self, context, instruction, at, *, event_id=None, seconds=FOLLOW_UP_SECONDS):
        core = self._current_core_turns(context, instruction, at)
        row = self.runtime.read_one("""SELECT b.*, f.state AS flow_state, f.deadline_at
            FROM conversation_flows f JOIN conversation_batches b ON b.batch_id = f.batch_id
            WHERE f.route_key = ? ORDER BY f.sequence DESC LIMIT 1""", (context.scope.key,))
        if core and (row is None or core[-1]["admitted_at"] >= row["created_at"]):
            speaker = context.actor.principal.native_account_id
            for turn in reversed(core):
                if row is not None and turn["admitted_at"] < row["created_at"]:
                    break
                if turn.get("event_id") == event_id:
                    continue
                result = self._core_continuation(context, turn, at, event_id=event_id, seconds=seconds)
                if result:
                    return result
                if not self._is_silent_skip_turn(turn, speaker):
                    return ""
            return ""
        if (row is None or row["origin_key"] != context.key or row["role_instruction"] != instruction
                or row["state"] in ("cancelled", "invalidated")
                or not batch_current(self.runtime, row["batch_id"], context.scope)):
            return ""
        admission = self.recorded(context, row["batch_id"])
        if admission and admission["reasons"] == ["repeat"]:
            return ""
        if row["flow_state"] in ("collecting", "generating", "delivering"):
            deadline = parse_moment(row["deadline_at"])
            return "active_follow_up" if deadline is not None and at < deadline else ""
        if row["flow_state"] != "complete":
            return ""
        receipt = self.runtime.read_one("""SELECT MAX(p.delivered_at) AS delivered_at
            FROM conversation_parts p JOIN conversation_sent_history h
                ON h.part_id = p.part_id AND h.batch_id = p.batch_id AND h.receipt_ref = p.receipt_ref
            JOIN conversation_delivery_receipts r ON r.part_id = p.part_id AND r.receipt_ref = p.receipt_ref
            WHERE p.batch_id = ? AND p.state = 'delivered' AND h.origin_key = ?""",
            (row["batch_id"], context.key))
        when = parse_moment(receipt["delivered_at"]) if receipt is not None else None
        if when is None or not 0 <= (at - when).total_seconds() <= seconds:
            return ""
        if self._intervening_message(context, when, at, event_id):
            return ""
        return "reply_follow_up"

    def _quoted_reply(self, context, identifiers, instruction, at):
        if not identifiers:
            return False
        for turn in self._current_core_turns(context, instruction, at):
            when = parse_moment(turn.get("returned_at"))
            if when is not None and when <= at and set(identifiers).intersection(turn.get("platform_ids", ())):
                return True
        rows = self.runtime.read_all("""SELECT p.batch_id, p.delivered_at FROM conversation_parts p
            JOIN conversation_sent_history h
                ON h.part_id = p.part_id AND h.batch_id = p.batch_id AND h.receipt_ref = p.receipt_ref
            JOIN conversation_delivery_receipts r ON r.part_id = p.part_id AND r.receipt_ref = p.receipt_ref
            WHERE p.state = 'delivered' AND p.platform_ref IN ("""
            + ",".join("?" for _ in identifiers) + ") LIMIT 128", tuple(identifiers))
        for row in rows:
            when = parse_moment(row["delivered_at"])
            if when is None or when > at:
                continue
            try:
                self.messages.group_context.original(row["batch_id"], context, instruction, self.messages.tasks.now())
            except TaskError:
                continue
            return True
        return False
