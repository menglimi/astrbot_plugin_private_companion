"""Original-role group participation using the existing model and tool receipts.

Accepted input remains group history whether the role speaks or keeps listening.
This owner performs no model or platform I/O and does not invent an empty reply.

It also owns the registered ``conversation.group_participation`` settings group:
the group observation family, the self-initiated interjection budget and the
repeat settings are read here. Ordinary participation contributes references to
the role prompt; literal follow/interrupt actions are decided in repeat_actions
and sent by the AstrBot host after authorization, without ordinary generation.
"""

from dataclasses import replace
from datetime import datetime
import json
import logging

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.diagnostics.inbound import InboundNoReplyLedger, UNMET
from companion_v2.execution.models import TaskError, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.memory.group.access import owner_key
from companion_v2.model_gateway.models import ToolSpec
from .group_interjection import (INTERJECTION, REPEAT_FOLLOW, REPEAT_INTERRUPT, GroupSpeechLedger,
                                 InterjectionPolicy, addresses_role, best_burst, day_key, interjection_state,
                                 openings, repeat_state)
from .group_observation import GroupPresenceStore, parse_moment, history_items, observe
from .group_admission import GroupReplyAdmission, REASONS
from .group_timing import ADDRESS_MARKER, RepeatPolicy, clean_group_text
from .participation_prompts import (group_hints, interjection_instruction, observation_instruction,
                                    repeat_instruction)
from .reply_settings import (group_air_settings, group_safety_settings,
                             group_wakeup_settings, validate as validate_reply_settings)
from . import group_high_intensity


ACTION = "group.participation.choose"
VERSION = "conversation.group_participation.v1"
SKIPPED = "group_participation_skipped"
logger = logging.getLogger(__name__)
INSTRUCTION = """以原人格旁听群聊，收到消息不等于有人在问你。结合话题、发言对象、已有回答和最近参与情况，判断此刻接话是否自然、有内容；他人之间的交流可以安静听着，不依赖固定关键词或回复配额。
明确 @ 你时正常回应；未被明确 @ 而准备接话时，先调用 group.participation.choose，mode=reply，reason 简短说明参与缘由（称呼、引用、求助、补话等），只记录原因，不向群里发判断过程。随后按需使用工具与回复。
不适合参与时调用 choose，mode=skip，reason 可省略。本轮到此结束，不发空消息、占位或“我选择不回复”。可以同时记录已授权的互动学习或反馈，不能先做业务操作再旁听，也不能用 skip 撤销已发生的操作。这个决定只针对本轮，不建立长期拒绝规则，不在无新消息时另起对话。
未点名、专名不明或缺少媒体内容时可以 skip；明确向你求助但材料不足时简短问清。若本轮有合适表情原图且足够表达即时态度，可先 reply 再只发图；表情不能代替问题答案。"""

CONFIG_GROUP = "conversation.group_participation"
CONFIG_OWNER = "conversation.group_participation"
# V1 kept these in its flat config and its group observation page. Defaults are
# V1's own, so an installation that never edits them behaves as before; the
# repeat window is V1's hard-coded 120 seconds made explicit, because V2 does not
# leave adjustable windows in code.
CONFIG_DEFAULTS = {
    "enable_group_social_context": False,
    "enable_group_topic_threads": True,
    "group_topic_thread_max": 12,
    "enable_group_mood_detection": False,
    "enable_group_roleplay_strength": False,
    "enable_group_moments": False,
    "enable_group_moment_portrait": False,
    "enable_group_joke_guard": False,
    "enable_group_persona_denoise": True,
    "enable_group_interjection": False,
    "group_interject_min_interval_minutes": 180,
    "group_interject_max_daily": 2,
    "enable_group_interjection_feedback": True,
    "group_interject_feedback_window_seconds": 600,
    "enable_group_repeat_follow": True,
    "group_repeat_count_distinct_users_only": False,
    "group_repeat_trigger_threshold": 4,
    "group_repeat_follow_probability": 0.18,
    "group_repeat_interrupt_probability": 0.10,
    "group_repeat_interrupt_probability_step": 0.12,
    "group_repeat_interrupt_text": "禁止复读",
    "group_repeat_interrupt_image_path": "",
    "group_repeat_window_seconds": 120,
    "group_context_max_age_hours": 12,
    "enable_group_behavior_learning": True,
    "enable_group_behavior_imitation": True,
    "enable_group_behavior_feedback": True,
    "group_behavior_guidance": "",
}
CONFIG_LIMITS = {"group_topic_thread_max": (3, 40),
                 "group_interject_min_interval_minutes": (0, 1440),
                 "group_interject_max_daily": (0, 20),
                 "group_interject_feedback_window_seconds": (60, 86400),
                 "group_repeat_trigger_threshold": (3, 20),
                 "group_repeat_window_seconds": (10, 600),
                 "group_context_max_age_hours": (1, 168)}
CONFIG_SWITCHES = tuple(key for key, value in CONFIG_DEFAULTS.items() if type(value) is bool)
CONFIG_COUNTS = tuple(key for key, value in CONFIG_DEFAULTS.items() if type(value) is int)
CONFIG_PROBABILITIES = tuple(key for key, value in CONFIG_DEFAULTS.items() if type(value) is float)
CONFIG_INTERRUPT_TEXT = "group_repeat_interrupt_text"
CONFIG_INTERRUPT_IMAGE = "group_repeat_interrupt_image_path"
MAX_INTERRUPT_TEXT = 80
MAX_INTERRUPT_IMAGE_PATH = 4096


def configuration_fields():
    """The settings this owner reads, declared once in the unified registry."""
    fields = []
    for name, default in CONFIG_DEFAULTS.items():
        low, high = CONFIG_LIMITS.get(name, (None, None))
        fields.append(FieldDefinition(
            CONFIG_GROUP + "." + name, CONFIG_GROUP, CONFIG_GROUP,
            {bool: ValueKind.BOOLEAN, int: ValueKind.INTEGER, float: ValueKind.PROBABILITY,
             str: ValueKind.TEXT}[type(default)], default,
            minimum=low, maximum=high, maximum_chars=1200 if name == "group_behavior_guidance" else None,
            apply_mode=ApplyMode.IMMEDIATE, core_only=True, owner_id=CONFIG_OWNER))
    return tuple(fields)


def validate_interrupt_text(text):
    """The one rule the registry cannot state: a single short, printable line.

    Shared by the read path and the owner's write path so the two cannot drift --
    a value this refuses must never be storable, and a stored value must never be
    what makes the next group turn raise.
    """
    if (type(text) is not str or not text.strip() or len(text) > MAX_INTERRUPT_TEXT
            or any(ord(char) < 32 for char in text)):
        raise ConfigurationError("group_participation_configuration",
                                 "the repeat interrupt text must be one short line")
    return text


def validate_interrupt_image_path(path):
    """An optional local host path, never a URL or a member-provided attachment."""
    if (type(path) is not str or len(path) > MAX_INTERRUPT_IMAGE_PATH
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise ConfigurationError("group_participation_configuration",
                                 "the repeat interrupt image must be a printable local path")
    return path


class GroupParticipationConfigurationOwner(SparseConfigurationOwner):
    """Window-scoped group participation settings.

    The open fields already carry their bounds in the registry, but
    ``group_repeat_interrupt_text`` does not: its rule also bans control
    characters and a blank line, and the contract publishes no string lengths.
    ``group_participation_settings`` re-checks it on every read, so without the
    two overrides below an out-of-range line would be accepted here and then make
    the whole group unreadable on the next turn.
    """

    owner_id = CONFIG_OWNER

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        _check_interrupt_text(result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction():
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            _check_interrupt_text(self.read(target, fields).fields)
            return result


def _check_interrupt_text(fields):
    """Validate both literal interruption settings before storing a snapshot."""
    item = fields.get(CONFIG_GROUP + "." + CONFIG_INTERRUPT_TEXT)
    if item is not None:
        validate_interrupt_text(item.value)
    image = fields.get(CONFIG_GROUP + "." + CONFIG_INTERRUPT_IMAGE)
    if image is not None:
        validate_interrupt_image_path(image.value)
    guidance = fields.get(CONFIG_GROUP + ".group_behavior_guidance")
    if guidance is not None and (type(guidance.value) is not str or len(guidance.value) > 1200):
        raise ConfigurationError("group_participation_configuration", "behavior guidance must be at most 1200 characters")


def group_participation_settings(runtime, context):
    """The effective settings for this exact group window.

    Read through the owner directly, exactly like the memory episode settings:
    the values are bounded reference material for one group turn, and the
    registered definitions are what the configuration surface validates.
    """
    target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                          context.role_binding.role, context.scope)
    snapshot = GroupParticipationConfigurationOwner(runtime).read(target, configuration_fields())
    settings = {key.removeprefix(CONFIG_GROUP + "."): item.value for key, item in snapshot.fields.items()}
    for name in CONFIG_SWITCHES:
        if type(settings[name]) is not bool:
            raise ConfigurationError("group_participation_configuration", "group participation switches must be boolean")
    for name in CONFIG_COUNTS:
        if type(settings[name]) is not int or isinstance(settings[name], bool) or not CONFIG_LIMITS[name][0] <= settings[name] <= CONFIG_LIMITS[name][1]:
            raise ConfigurationError("group_participation_configuration", f"{name} is outside its declared range")
    for name in CONFIG_PROBABILITIES:
        value = settings[name]
        if type(value) not in (int, float) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ConfigurationError("group_participation_configuration", f"{name} must be a fraction between 0 and 1")
    validate_interrupt_text(settings[CONFIG_INTERRUPT_TEXT])
    validate_interrupt_image_path(settings[CONFIG_INTERRUPT_IMAGE])
    if type(settings["group_behavior_guidance"]) is not str or len(settings["group_behavior_guidance"]) > 1200:
        raise ConfigurationError("group_participation_configuration", "behavior guidance must be at most 1200 characters")
    return settings


class GroupParticipation:
    actions = frozenset({ACTION})
    catalog = (ToolSpec(ACTION, "按原人格决定本轮群聊参与或安静旁听；skip 可与互动学习、反馈笔记同批调用，不能与业务操作同批或在其后调用。",
        encode({"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["reply", "skip"]},
            "reason": {"type": "string", "maxLength": 256}},
            "required": ["mode"], "additionalProperties": False})),)

    def __init__(self, messages):
        self.messages, self.runtime, self.tasks = messages, messages.runtime, messages.tasks
        self.records = GroupPresenceStore(self.runtime)
        self.speech = GroupSpeechLedger(self.runtime)
        self.admission = GroupReplyAdmission(messages)
        from .behavior import GroupBehavior
        self.behavior = GroupBehavior(messages)
        self._frozen_turns = {}
        self._reflection_day = ""

    def advance_reflection(self):
        """Clean recoverable placeholder artifacts once per group-local day."""
        day = day_key(self.tasks.now(), self.messages.timezone_name)
        if day == self._reflection_day:
            return 0
        self._reflection_day = day
        try:
            return self.records.clean_media_placeholders()
        except Exception:
            return 0

    def catalog_for(self, context):
        if context.scope.kind is not ScopeKind.GROUP:
            return ()
        self.messages.group_context.authorize(context, self.tasks.now())
        return self.catalog

    def augment(self, batch, context, request, at):
        self.messages.group_context.authorize(context, at)
        instruction = request.instruction + "\n" + INSTRUCTION
        admission = self.admission.recorded(context, batch["batch_id"])
        if (admission is not None and not admission["mentioned"]
                and any(reason != "role_choice" for reason in admission["reasons"])):
            reasons = "、".join(REASONS[reason] for reason in admission["reasons"] if reason in REASONS)
            instruction += ("\n本轮没有明确 @，入站候选依据是：" + reasons
                            + "。候选只表示可以考虑参与；仍须按当前语境判断，不适合就 skip，"
                            "决定接话时先记录 mode=reply 和简短 reason。")
            if any(reason in ("active_follow_up", "reply_follow_up") for reason in admission["reasons"]):
                instruction += ("这是机器人上一轮互动后的非点名接话候选，发言者可能是另一位群成员；结合上一轮群聊上下文，"
                                "若仍在回答你的追问、补充刚才未说完的信息或顺着刚才与你的话头继续交流，可自然接住（mode=reply）；"
                                "若对话已收尾或对方已转向他人，则选择 skip。时间接近和话题相关都不等于仍在向你说话，"
                                "不要为了保持对话而逐句跟答，也不必反复回应礼貌收尾。")
        saved = json.loads(batch["contributions_json"])
        captured = saved.get("reply_settings") if isinstance(saved, dict) else None
        if captured is not None:
            if type(captured) is not dict or type(captured.get("settings")) is not dict:
                raise TaskError("group_participation_settings", "original reply settings are invalid")
            validate_reply_settings(captured["settings"])
        settings = captured["settings"] if captured is not None else {}
        # The hints freeze on their first build for this batch. A mid-turn
        # group-memory write may retire dependent history materials, and
        # recomputing derived guidance from the shrunken snapshot would break the
        # turn's instruction prefix contract. Request builds run inside a read
        # snapshot, so the freeze lives in memory; a restart recovers with a new
        # session that re-anchors its own first build.
        frozen = self._frozen_turns.get(batch["batch_id"])
        if frozen is None:
            frozen = self._build_turn(context, request, settings)
            if len(self._frozen_turns) >= 32:
                self._frozen_turns.pop(next(iter(self._frozen_turns)))
            self._frozen_turns[batch["batch_id"]] = frozen
        return replace(request, instruction=instruction + frozen["hints"])

    def air_settings(self, settings):
        """Expose the air-guard values the high-intensity gate reads.

        The topic gate needs the same window/threshold as the closeout; keeping
        one reader avoids a second, drifting copy of those defaults.
        """
        return group_air_settings(settings)

    def high_intensity_state(self, context, instruction, *, at, settings):
        """Read the opt-in hard closeout from confirmed role turns.

        The window/threshold/cooldown live in the air-guard group so V2 does not
        grow a second set of near-identical settings.  No merge queue is kept
        here: the ordinary group turn path still owns message batching.
        """
        air = group_air_settings(settings)
        if not air["group_high_intensity_enabled"]:
            return {"active": False, "recent_wakeups": 0, "threshold": 0,
                    "window_seconds": 0, "cooldown_seconds": 0, "until": "",
                    "remaining_seconds": 0}
        _, turns = self.admission.native_participation_history(
            context, instruction, at=at)
        rounds = [when for turn in turns
                  if (when := parse_moment(turn.get("returned_at"))) is not None]
        return group_high_intensity.state(
            rounds, at,
            window_seconds=air["group_air_guard_window_seconds"],
            threshold=max(2, air["group_air_guard_max_bot_replies"]),
            cooldown_seconds=air["group_air_guard_cooldown_seconds"],
        )

    def _build_turn(self, context, request, settings):
        """Derive every participation reference for one group turn, without writing.

        The repeated-history read stays exactly as it was; the observation,
        interjection and repeat references are additive and derive only from that
        same admitted material plus the registered settings.
        """
        # The original input time is the time base on every continuation and recovery.
        at = request.event.received_at
        recent = tuple(json.loads(material.payload) for material in request.context.materials
            if material.purpose == "group_conversation_history")
        parts = tuple(item["part_id"] for item in recent if item.get("type") == "confirmed_group_reply")
        batches = {row["part_id"]: row["batch_id"] for row in self.runtime.read_all(
            "SELECT part_id, batch_id FROM conversation_parts WHERE part_id IN ("
            + ",".join("?" for _ in parts) + ")", parts)} if parts else {}
        if set(batches) != set(parts):
            raise TaskError("group_participation_history", "confirmed reply history lost its original batch")
        texts = [clean_group_text(material.payload) for material in request.context.materials
                 if material.purpose == "current_source" or material.purpose.startswith("merged_input:")]
        current = " ".join(texts)
        hints = group_hints(request, context, settings, recent, batches, current_text=current)
        values = group_participation_settings(self.runtime, context)
        interjection = self._interjection_policy(values, settings)
        repeat_policy = self._repeat_policy(values)
        owner = owner_key(context)
        speaker = context.actor.principal.native_account_id
        visible = (*history_items(recent),
                   *({"kind": "member", "speaker_id": speaker, "received_at": at.isoformat(), "text": text}
                     for text in texts))
        safety = group_safety_settings(settings)
        observation = observe(self.records, owner=owner, settings=values, items=visible,
                              at=at, current_speaker=speaker)
        hints += observation_instruction(values, observation["facts"], safety)
        resolved = self.speech.resolve(self.speech.load(owner), at=at, day=day_key(at, self.messages.timezone_name),
                                       policy=interjection, items=visible, origin_key=context.key,
                                       timezone_name=self.messages.timezone_name)
        mood = (observation["facts"].get("mood") or {}).get("top_mood", "")
        facts = interjection_state(interjection, resolved["record"], at=at,
                                   initiative=bool(current) and not addresses_role(current, self._address_words(settings)),
                                   reasons=openings(current, mood_top=mood),
                                   interest_probability=group_wakeup_settings(settings)["group_wakeup_interest_probability"])
        hints += interjection_instruction(facts)
        repeat = repeat_state(repeat_policy, resolved["record"], at=at,
                              burst=best_burst(visible, texts, at=at, policy=repeat_policy))
        hints += repeat_instruction(repeat)
        act, signature = self._act_for(repeat, facts)
        return {"hints": hints, "owner": owner, "observation": observation["records"], "speech": resolved["record"],
                "mentioned": any(ADDRESS_MARKER in text for text in texts),
                "day": day_key(at, self.messages.timezone_name), "act": act, "act_signature": signature}

    def observe_early(self, context, *, text, speaker_id, received_at, event_id=None):
        """Fold one authenticated group message before reply admission.

        V1's observation hook ran even when its later reply path chose to stay
        quiet or could not prepare an optional attachment.  V2 used to defer
        this fold until ``_record`` after a generation turn, which meant a
        failed reply made the group invisible to the next turn.  This method is
        intentionally model-free and send-free: it only writes the bounded
        observation records that are enabled for this group window.
        """
        if context.scope.kind is not ScopeKind.GROUP:
            return False
        text = clean_group_text(text)
        if type(speaker_id) is not str or not speaker_id.strip():
            return False
        if not isinstance(received_at, datetime) or received_at.tzinfo is None:
            return False
        from .reset import boundary
        reset = boundary(self.runtime, context.scope)
        if reset is not None and stamp(received_at) <= reset["reset_at"]:
            return False
        self.admission.observe(context, text=text, speaker_id=speaker_id, received_at=received_at, event_id=event_id)
        if not text:
            return False  # media-only messages break the burst, without becoming expression evidence
        try:
            settings = group_participation_settings(self.runtime, context)
            self.behavior.observe(context, text=text, speaker_id=speaker_id, received_at=received_at, event_id=event_id)
            item = history_items(({
                "type": "group_member_message",
                "speaker_id": speaker_id,
                "received_at": stamp(received_at),
                "text": text,
            },))[0]
            observation = observe(self.records, owner=owner_key(context), settings=settings,
                                  items=(item,), at=received_at, current_speaker=speaker_id)
            records = observation["records"]
            if not records:
                return False
            with self.runtime.transaction() as connection:
                for kind, document in records.items():
                    self.records.write(connection, owner_key(context), kind, document)
            return True
        except Exception:
            # Observation is an additive reference. A malformed optional setting
            # or a transient metadata write must never reject the authenticated
            # group route or change the reply decision.
            return False

    @staticmethod
    def _address_words(settings):
        wakeup = group_wakeup_settings(settings)
        name = wakeup["group_wakeup_bot_name"] if wakeup["group_wakeup_bot_name_enabled"] else ""
        return tuple(dict.fromkeys((*wakeup["group_wakeup_direct_words"], *((name,) if name else ()))))

    @staticmethod
    def _interjection_policy(values, settings):
        return InterjectionPolicy(
            enabled=values["enable_group_interjection"],
            min_interval_seconds=values["group_interject_min_interval_minutes"] * 60,
            max_daily=values["group_interject_max_daily"],
            feedback_enabled=values["enable_group_interjection_feedback"],
            feedback_window_seconds=values["group_interject_feedback_window_seconds"],
            address_words=GroupParticipation._address_words(settings))

    @staticmethod
    def _repeat_policy(values):
        return RepeatPolicy(
            enabled=values["enable_group_repeat_follow"],
            threshold=values["group_repeat_trigger_threshold"],
            distinct_users_only=values["group_repeat_count_distinct_users_only"],
            follow_probability=values["group_repeat_follow_probability"],
            interrupt_probability=values["group_repeat_interrupt_probability"],
            interrupt_step=values["group_repeat_interrupt_probability_step"],
            interrupt_text=values["group_repeat_interrupt_text"],
            window_seconds=values["group_repeat_window_seconds"],
            cooldown_seconds=values["group_interject_min_interval_minutes"] * 60,
            max_daily=values["group_interject_max_daily"])

    @staticmethod
    def _act_for(repeat, facts):
        """Which self-initiated act this turn would count as, and its burst.

        The switch stays the feature gate: with interjection off, an ordinary
        unaddressed reply is not booked as an interjection. Repeat acts are
        booked whenever their own rule fired, because V1 counted both kinds
        against the one per-group daily budget.
        """
        action = repeat["decision"].get("action")
        if action == "follow":
            return REPEAT_FOLLOW, repeat["burst"]["signature"]
        if action == "interrupt":
            return REPEAT_INTERRUPT, repeat["burst"]["signature"]
        if facts["enabled"] and facts["initiative"]:
            return INTERJECTION, ""
        return None, ""

    def _record(self, context, batch_id, *, replied):
        """Fold this turn's bounded observation and self-speech bookkeeping.

        Derived material only: it is written once the turn really decided
        something, and a storage failure here is skipped rather than allowed to
        reject a reply that already passed every governance check.
        """
        if context.scope.kind is not ScopeKind.GROUP:
            return
        frozen = self._frozen_turns.get(batch_id)
        if frozen is None:
            return
        try:
            at = self.tasks.now()
            with self.runtime.transaction() as connection:
                for kind, document in frozen["observation"].items():
                    self.records.write(connection, frozen["owner"], kind, document)
                speech = frozen["speech"]
                if replied and frozen["act"] is not None:
                    speech = self.speech.act(speech, kind=frozen["act"], batch_id=batch_id, at=at,
                                             day=frozen["day"], signature=frozen["act_signature"])
                self.speech.persist(connection, frozen["owner"], speech)
        except Exception:
            return  # optional derived material never rejects a decided group turn
        self._frozen_turns.pop(batch_id, None)

    def record_no_reply(self, context, batch_id):
        """Persist a completed silent group turn without counting bot speech."""
        self._record(context, batch_id, replied=False)

    def diagnostics(self, scope, role, limit):
        """Projection for an authorized manager; never trims receipts or exposes message bodies."""
        rows = self.runtime.read_all("""SELECT b.batch_id, b.created_at, b.part_count,
            COALESCE(f.state, b.state) AS state, f.reason,
            (SELECT json_extract(c.result_json, '$.payload.mode') FROM conversation_tool_calls c
             WHERE c.batch_id = b.batch_id AND c.action = ? AND c.state = 'succeeded'
             ORDER BY c.sequence LIMIT 1) AS choice
            FROM conversation_batches b LEFT JOIN conversation_flows f ON f.batch_id = b.batch_id
            WHERE json_extract(b.context_json, '$.role_binding.scope') = ?
              AND json_extract(b.context_json, '$.role_binding.role') = ?
            ORDER BY b.created_at DESC, b.rowid DESC LIMIT ?""", (ACTION, encode(scope), encode(role), limit))
        return tuple({"batch_id": row["batch_id"], "created_at": row["created_at"], "state": row["state"],
            "reason": row["reason"], "mode": row["choice"] or ("reply" if row["part_count"] else "pending")}
            for row in rows)

    @staticmethod
    def _arguments(arguments):
        if (type(arguments) is not dict or set(arguments) - {"mode", "reason"}
                or arguments.get("mode") not in ("reply", "skip")
                or type(arguments.get("reason", "")) is not str or len(arguments.get("reason", "")) > 256):
            raise TaskError("group_participation_arguments", "participation requires its bounded explicit choice")
        return arguments["mode"], arguments.get("reason", "")

    def _rows(self, batch_id):
        return self.runtime.read_all("SELECT * FROM conversation_tool_calls WHERE batch_id = ? ORDER BY sequence", (batch_id,))

    def _proof(self, row, context):
        batch = self.messages.owned(context, row["batch_id"])
        if row["action"] != ACTION or row["task_id"] != batch["generation_task_id"]:
            raise TaskError("group_participation_changed", "the choice must belong to the original group generation")
        arguments = json.loads(row["arguments_json"])
        mode, reason = self._arguments(arguments)
        original = self.runtime.read_one("""SELECT tool_calls_json FROM model_rounds
            WHERE task_id = ? AND call_id = ? AND attempt_id = ? AND state = 'tools'""",
            (row["task_id"], row["model_call_id"], row["attempt_id"]))
        call = {"call_id": row["tool_call_id"], "action": ACTION, "arguments_json": row["arguments_json"]}
        calls = json.loads(original["tool_calls_json"]) if original else []
        if call not in calls:
            raise TaskError("group_participation_unproven", "the choice requires its original model request")
        if mode == "skip" and calls != [call]:
            raise TaskError("group_participation_mixed", "listening must be selected without another tool in the same round")
        return {"state": "succeeded", "effect": "none", "payload": {
            "version": VERSION, "batch_id": row["batch_id"], "context_key": context.key,
            "mode": mode, "reason": reason}}

    def execute(self, context, batch_id, call_id, action, arguments):
        self.messages.group_context.authorize(context, self.tasks.now())
        if action != ACTION:
            raise TaskError("group_participation_action", "the action is not owned by group participation")
        mode, _ = self._arguments(arguments)
        rows = self._rows(batch_id)
        current = next((r for r in rows if r["tool_call_id"] == call_id), None)
        if current is None or json.loads(current["arguments_json"]) != arguments:
            raise TaskError("group_participation_unproven", "the choice needs an admitted original tool call")
        result = self._proof(current, context)
        previous = self._decision(context, batch_id, tuple(r for r in rows if r["sequence"] < current["sequence"]))
        if previous["mode"] == "skip" or mode == "skip" and previous["mode"] == "reply":
            raise TaskError("group_participation_fixed", "the original participation choice cannot be reversed")
        return result, None

    def validate(self, row, context, result):
        self.messages.group_context.authorize(context, self.tasks.now())
        if row["state"] != "succeeded":
            return result, None
        if result != self._proof(row, context) or row["effect"] != "none" or row["operation_id"] is not None:
            raise TaskError("group_participation_changed", "the original participation receipt changed")
        return result, None

    def _decision(self, context, batch_id, rows):
        selected = None
        for row in rows:
            if row["action"] != ACTION:
                if selected is not None and selected["mode"] == "skip":
                    raise TaskError("group_participation_changed", "business work cannot follow an original skip")
                if selected is None:
                    selected = {"mode": "reply", "basis": "business_tool", "call_id": row["model_call_id"]}
            elif row["state"] == "succeeded":
                result, _ = self.validate(row, context, json.loads(row["result_json"]))
                mode = result["payload"]["mode"]
                if selected is not None and (selected["mode"] == "skip" or mode == "skip"):
                    raise TaskError("group_participation_changed", "the original participation choice cannot be replaced")
                if selected is None:
                    selected = {"mode": mode, "basis": "role_choice", "call_id": row["model_call_id"]}
            elif row["state"] == "failed" and selected is None:
                arguments = json.loads(row["arguments_json"])
                if arguments.get("mode") != "skip":
                    continue
                if row["effect"] != "none" or row["operation_id"] is not None:
                    raise TaskError("group_participation_changed", "a failed silent choice cannot carry an effect")
                try:
                    result = self._proof(row, context)
                except TaskError as error:
                    if error.code == "group_participation_mixed":
                        continue
                    raise
                if result["payload"]["mode"] == "skip":
                    selected = {"mode": "skip", "basis": "failed_role_choice",
                                "call_id": row["model_call_id"]}
        return selected or {"mode": "pending", "basis": "no_decision"}

    def decision(self, context, batch_id):
        """Read control metadata through the original group authority, not model prose."""
        self.messages.group_context.authorize(context, self.tasks.now())
        batch = self.messages.owned(context, batch_id)
        selected = self._decision(context, batch_id, self._rows(batch_id))
        final = self.runtime.read_one("SELECT call_id FROM model_rounds WHERE task_id = ? AND state = 'final' ORDER BY round_no LIMIT 1",
                                      (batch["generation_task_id"],))
        if final:
            if selected["mode"] == "skip" and selected["basis"] != "failed_role_choice":
                raise TaskError("group_participation_changed", "an original skip cannot contain a final reply")
            if selected["mode"] == "pending":
                selected = {"mode": "reply", "basis": "final_reply", "call_id": final["call_id"]}
        return selected

    def finish_if_skipped(self, context, batch_id):
        """Complete only a proven silent choice; recovery never invokes the model again."""
        if context.scope.kind is not ScopeKind.GROUP:
            return False
        with self.runtime.transaction() as connection:
            decision = self.decision(context, batch_id)
            if decision["mode"] != "skip":
                return False
            require(self.tasks.check_generation())
            batch = self.messages.owned(context, batch_id)
            if batch["state"] != "sealed" or connection.execute(
                    "SELECT 1 FROM conversation_parts WHERE batch_id = ?", (batch_id,)).fetchone():
                raise TaskError("group_participation_changed", "a silent choice cannot contain a reply manifest")
            # Recheck the same sealed sources, member grants and persona. The
            # skip is durable metadata, never permission to use revoked context.
            self.messages._request(batch, context, self.tasks.now(), include_tools=False)
            connection.execute("""UPDATE conversation_flows SET state = 'complete', reason = ?, due_at = ?
                WHERE batch_id = ? AND state IN ('collecting', 'generating', 'delivering')""",
                (SKIPPED, stamp(self.tasks.now()), batch_id))
        self._record(context, batch_id, replied=False)
        self._resolve_silence(batch, context, batch_id, decision)
        return True

    def _resolve_silence(self, batch, context, batch_id, decision):
        """Report the role's own decision to keep listening, and nothing else.

        This is a legitimate decision, so it is logged as information with the
        evidence that produced it: which control path proved the choice, the
        bounded reason the model itself wrote, and how long the turn took.
        """
        try:
            reason = self._silence_reason(context, batch_id)
            at = self.tasks.now()
            started_at = datetime.fromisoformat(batch["created_at"])
            InboundNoReplyLedger(self.runtime).emit(
                expectation=UNMET, stage="silence", turn=batch_id, reason_code="silence",
                observed_at=at, origin_key=batch["origin_key"], level="info",
                code="basis_" + str(decision["basis"]),
                ms=max(0.0, (at - started_at).total_seconds() * 1000))
        except Exception:
            return  # diagnosis is additive: a silent turn is already complete

    def _silence_reason(self, context, batch_id):
        """The model's own bounded skip reason, or ``None`` when it wrote none."""
        row = self.runtime.read_one("""SELECT result_json FROM conversation_tool_calls
            WHERE batch_id = ? AND action = ? AND state = 'succeeded'
            ORDER BY sequence LIMIT 1""", (batch_id, ACTION))
        document = json.loads(row["result_json"]) if row is not None else {}
        reason = document.get("payload", {}).get("reason") if isinstance(document, dict) else None
        return " ".join(reason.split())[:64] if isinstance(reason, str) else None

    def allow_reply(self, context, batch_id):
        if context.scope.kind is ScopeKind.GROUP and self.decision(context, batch_id)["mode"] == "skip":
            raise TaskError("group_participation_fixed", "a skipped group turn cannot produce a reply")
        self.log_reply_reason(context, batch_id, stage="decision")
        self._record(context, batch_id, replied=True)

    def log_reply_reason(self, context, batch_id, *, stage):
        if context.scope.kind is not ScopeKind.GROUP:
            return
        saved = self.admission.recorded(context, batch_id)
        if saved is None:
            frozen = self._frozen_turns.get(batch_id, {})
            self.admission.record(context, batch_id, reason="role_choice", mentioned=frozen.get("mentioned", False))
            saved = self.admission.recorded(context, batch_id)
        if saved["mentioned"]:
            return
        choice = self.runtime.read_one("""SELECT result_json FROM conversation_tool_calls
            WHERE batch_id = ? AND action = ? AND state = 'succeeded'
              AND json_extract(result_json, '$.payload.mode') = 'reply'
              AND trim(json_extract(result_json, '$.payload.reason')) != ''
            ORDER BY sequence LIMIT 1""", (batch_id, ACTION))
        model_reason = json.loads(choice["result_json"])["payload"]["reason"] if choice else ""
        policy_reason = "、".join(REASONS[reason] for reason in saved["reasons"] if reason in REASONS)
        reason = " ".join((model_reason or policy_reason or REASONS["role_choice"]).split())[:256]
        if self.admission.mark_logged(context, batch_id, stage):
            logger.info("[CompanionV2] 非@群回复原因 batch_ref=%s stage=%s trigger=%s reason=%s reason_source=%s",
                        batch_id, stage, ",".join(saved["reasons"]), json.dumps(reason, ensure_ascii=False),
                        "model" if model_reason else "policy")
