"""One explicit opportunity, one supervised model decision, one durable delivery.

Trusted producers, including the conversation scheduler, supply the cause and
current persona; interrupted model work is never silently replayed.
"""

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from zoneinfo import ZoneInfo

from companion_v2.capabilities.memory_push import (DEFAULT_MEMORY_PUSH, PUSH_PROACTIVE_MESSAGE, MemoryPushRecord,
    push_memory, push_memory_id)
from companion_v2.configuration.models import ConfigurationError
from companion_v2.conversation.messages import source_from_document
from companion_v2.conversation.reply_preparation import observed_statements, trim_blank_edges
from companion_v2.conversation.reply_settings import ReplySettings, voice_policy
from companion_v2.conversation.segmentation import SegmentationPolicy, part_gap_seconds
from companion_v2.conversation.text_processing import (TEXT_DEFAULTS, strip_internal_expression_protocol,
                                                       text_policy)
from companion_v2.conversation.voice_reply import VoiceReplyPolicy, strip_voice_markup
from companion_v2.delivery.media import image_manifest, manifest_hash
from companion_v2.delivery.plan import DeliveryContent, PokePlan, VoicePlan
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.delivery.history import HistoricalText, VERSION as HISTORY_VERSION
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskDefinition, TaskError,
    TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp)
from companion_v2.execution.runner import ExactTaskRunner, RunResult
from companion_v2.execution.service import outcome
from companion_v2.expression.configuration import read_settings as expression_settings
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, VersionRef, normalize_utc
from companion_v2.model_gateway.models import ModelTaskProfile, is_released
from companion_v2.relationships.private_access import REVOCABLE_ABSENCE_CODES
from companion_v2.role_work.state import humanized
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .configuration import GROUP
from .budget import DIAGNOSTIC_TASK_PREFIX, ContactBudget, local_day_start
from .contact_models import ContactIntent, ContactJob
from .inspirations import ProactiveInspirations
from .persona_judge import (KIND as JUDGE_KIND, PersonaJudge, JudgeVerdict, exempt_purposes, judge_output,
                            judge_request, signature_for)
from .platform_circuit import ProactivePlatformCircuit
from .news_events import SOURCE_OWNER as NEWS_SOURCE_OWNER, news_provenance
from .planner import ProactivePlanner
from .preferences import ProactivePreferences
from .prompts import (contact_output, history_round_groups, news_presentation_instruction,
                      prompt_sections, request_for)
from .official_alert_events import SOURCE_OWNER as OFFICIAL_ALERT_SOURCE_OWNER, attribution_suffix


CONTACT_KIND = "proactive.compose"
JUDGE_TIMEOUT_SECONDS = 90
_SEGMENTATION_DEFAULTS = asdict(SegmentationPolicy())
_SEGMENTATION_KEYS = frozenset(_SEGMENTATION_DEFAULTS)
_SEGMENTATION_SETTINGS_KEYS = _SEGMENTATION_KEYS | frozenset((
    "part_gap_ms", "part_gap_mode", "part_gap_min_ms", "part_gap_max_ms", "part_gap_log_base",
    "state_pacing_factor", "role_energy", "role_sleepy"))
logger = logging.getLogger(__name__)


def role_state_for_prompt(summary, purpose):
    """Keep bodily status out of generic proactive contact prompts.

    Hunger and health are useful to meal, care or environment contacts, but a
    playful or chatty contact that receives "hungry" as a live field tends to
    turn it into a status report and invent the user's eating habits. Keep the
    rest of the already-reviewed state material intact so pacing and sleep
    presentation continue to use the same contract.
    """

    if not isinstance(summary, dict) or purpose is None:
        return summary
    result = dict(summary)
    purpose = str(purpose)
    if purpose not in ("meal", "environment"):
        result.pop("hunger", None)
    if purpose not in ("meal", "care", "environment"):
        result.pop("health", None)
    return result or None


def contact_profile(provider, *, max_output_tokens=1024):
    return ModelTaskProfile(CONTACT_KIND, Lane.BACKGROUND, provider,
        "保持原人格；基于真实来源决定是否主动联系，允许没有合适内容时不发送。",
        max_output_tokens=max_output_tokens, prompt_sections=prompt_sections())


def _push_scope(kind):
    """推送用的会话口径：对外只区分私聊与群聊，其余一律 ``unknown``。

    V2 自己的 ScopeKind 里还有 thread/device/system，那些不是接收方认识的口径；
    硬塞过去只会让它在可见性判定里走进一条没人验证过的分支。
    """

    value = getattr(kind, "value", "")
    return value if value in ("private", "group") else "unknown"


def persona_judge_profile(provider, *, max_output_tokens=260):
    """The post-draft persona judgement, as its own registered background task.

    The host registers this profile next to the contact profile; without it the
    judgement is unavailable and an enabled judge withholds the contact instead
    of sending it unjudged.
    """
    return ModelTaskProfile(JUDGE_KIND, Lane.BACKGROUND, provider,
        "按当前人格与世界观点评一条已写好的主动消息，只决定放行或拦下。", max_output_tokens=max_output_tokens)


def _segmentation_policy(settings):
    values = settings if isinstance(settings, dict) else {}
    return SegmentationPolicy(**{key: values.get(key, default)
                                 for key, default in _SEGMENTATION_DEFAULTS.items()})


class ProactiveContacts:
    def __init__(self, configuration, memory_push=None):
        self.configuration, self.runtime = configuration, configuration.runtime
        # 推送端口是旁路：它记录角色说过的话，但不参与「说不说、说什么」的决定。
        self.memory_push = DEFAULT_MEMORY_PUSH if memory_push is None else memory_push
        self.sources, self.operations = SourceRepository(self.runtime), OperationRepository(self.runtime)
        self.platform_circuit = ProactivePlatformCircuit(self.runtime)
        self.budget = ContactBudget(self.runtime)
        self.history = self.schedule = self.semantics = None
        self.inspirations = ProactiveInspirations(self)
        # Optional: an unwired host or an unavailable memory leaves proactive
        # contact exactly as it was, and a memory failure never withholds a
        # message the user may be waiting for.
        self.memory_context = None
        self.role_state = self.life_context = None
        self.expression_context = self.expression_review = None
        self.persona_judge = PersonaJudge(self.runtime)
        self._pending, self._invocations = {}, set()
        self._closed = False
        self.gateway = self.runner = self.generation = self.judge_runner = None
        self.registrations = (TaskRegistration(TaskDefinition(CONTACT_KIND, GROUP, GROUP, Lane.BACKGROUND,
            side_effect=SideEffect.LOCAL, binding=ProviderBinding(GROUP, "local", "1", "contact-worker-1"),
            timeout_seconds=180, max_attempts=1, concurrency=2), self.validate),
            TaskRegistration(TaskDefinition(JUDGE_KIND, GROUP, GROUP, Lane.BACKGROUND,
                side_effect=SideEffect.LOCAL, binding=ProviderBinding(GROUP, "local", "1", "contact-judge-1"),
                timeout_seconds=JUDGE_TIMEOUT_SECONDS, max_attempts=1, concurrency=2), self.validate_judge))

    def attach(self, gateway, delivery, *, require_running, notify):
        if self.gateway is not None or gateway.runtime is not self.runtime or delivery.runtime is not self.runtime:
            raise ValueError("contact worker must share its original gateway and delivery runtime")
        self.gateway, self.delivery, self.tasks = gateway, delivery, gateway.tasks
        self.require_running, self.notify = require_running, notify
        self.preferences = ProactivePreferences(self.configuration, self.tasks)
        self.runner = ExactTaskRunner(self.tasks, frozenset({CONTACT_KIND}), self._generate)
        self.judge_runner = ExactTaskRunner(self.tasks, frozenset({JUDGE_KIND}), self._judge_generate)

    def attach_semantics(self, semantics):
        if self.semantics is not None or semantics.app.runtime is not self.runtime:
            raise ValueError("contact reactions require the original semantic service")
        self.semantics = semantics

    def attach_memory(self, memory):
        """Bind the relationship's memory read owner, exactly as replies do.

        The host passes the same service ``ConversationMessages`` receives, so a
        proactive message and a reply read one memory. The binding fails loudly
        at construction time instead of silently dropping every later contact's
        memory material, but it stays optional: a host that never calls this
        keeps today's behaviour.
        """
        if memory is None or memory.runtime is not self.runtime:
            raise ValueError("proactive memory must use the contact worker's original runtime")
        if self.memory_context is not None and self.memory_context is not memory.context:
            raise ValueError("proactive memory owner cannot be replaced")
        self.memory_context = memory.context

    def _memory_materials(self, job, records):
        """Recall this relationship's memory for one contact, or nothing.

        Read-only and best effort: a disabled memory, a moved role binding, a
        forgotten source or any storage failure degrades to "no material"
        instead of stopping the message. ``job.context.actor`` is already the
        authenticated owner of this opportunity, and every read rechecks it.
        """
        if self.memory_context is None:
            return ()
        try:
            sources = tuple(record.reference for record in records)
            session_key = "memory-contact-" + digest(job.request_hash)
            # The opportunity's own possibility and the exact selected source
            # bodies are this contact's current evidence, so topical recall and
            # the habit candidates see the same source material a reply would
            # see. The key stays bounded because it is
            # derived from the task itself, never from model text.
            snapshot = self.memory_context.contact_capture(job.context, session_key, sources, job.deadline_at)
            return self.memory_context.contact_contributions(snapshot, job.context)
        except Exception as error:
            # Memory is optional material for a message the user may already be
            # waiting for: an unavailable owner or an unexpected storage failure
            # must degrade to "no material", never to a withheld contact.
            logger.warning("V2 proactive memory recall was unavailable: %s", type(error).__name__)
            return ()

    def _capture_expression(self, context, at):
        if self.expression_context is None:
            return None
        try:
            return self.expression_context.capture(context, at, policy=expression_settings(self.runtime, context),
                                                  channel="proactive")
        except Exception as error:
            logger.warning("V2 proactive expression capture unavailable: %s", type(error).__name__)
            return None

    def _expression_materials(self, job, contact_id):
        if self.expression_context is None or job.expression_style is None or job.administrator_test:
            return ()
        try:
            return self.expression_context.contributions(job.expression_style, job.context, self.tasks.now(),
                policy=expression_settings(self.runtime, job.context), batch_id=contact_id)
        except Exception as error:
            logger.warning("V2 proactive expression rules unavailable: %s", type(error).__name__)
            return ()

    def _role_state_summary(self, context, at):
        if self.role_state is None:
            return None
        try:
            state = self.role_state.read(context, at=at)
        except Exception:
            return None
        if not isinstance(state, dict) or state.get("state") in ("disabled", "unknown"):
            return None
        values = state.get("values") or {}
        if not isinstance(values, dict):
            return None
        summary = {}
        if isinstance(values.get("energy"), int):
            summary["energy"] = values["energy"]
        for key in ("sleep", "busy", "hunger", "health", "location"):
            if isinstance(values.get(key), str) and values[key].strip():
                summary[key] = values[key].strip()[:120]
        mood_val = values.get("mood")
        if isinstance(mood_val, str) and mood_val.strip():
            summary["mood"] = mood_val.strip()[:120]
        elif isinstance(mood_val, dict):
            desc = mood_val.get("description")
            if isinstance(desc, str) and desc.strip():
                summary["mood"] = desc.strip()[:120]
            axes = mood_val.get("axes")
            if isinstance(axes, dict) and axes:
                summary["mood_axes"] = {k: round(float(v), 2) for k, v in axes.items()
                                        if isinstance(v, (int, float))}
        conditions = [item.get("text", "").strip()[:120] for item in (values.get("conditions") or ())
                      if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("text").strip()]
        if conditions:
            summary["conditions"] = conditions[:4]
        sleep = state.get("sleep_execution")
        if isinstance(sleep, dict):
            summary["sleep_execution"] = {key: sleep[key] for key in (
                "state", "awake_minutes", "sleep_pressure", "sleep_debt_minutes") if key in sleep}
        return summary or None

    def _recent_life_summary(self, context, at):
        if self.life_context is None:
            return None
        try:
            candidates = self.life_context.candidates(context, at)
        except Exception:
            return None
        items = []
        for variants in candidates.variants[:3]:
            if not variants:
                continue
            try:
                data = json.loads(variants[0].payload)
            except Exception:
                continue
            entry = {"kind": data.get("kind"), "basis": data.get("basis")}
            for key in ("state", "description", "local_date", "summary", "afterglow"):
                if isinstance(data.get(key), str) and data[key].strip():
                    entry[key] = data[key].strip()[:160]
            reading = data.get("reading")
            if isinstance(reading, dict) and isinstance(reading.get("reflection"), str) and reading["reflection"].strip():
                entry["reflection"] = reading["reflection"].strip()[:160]
            if len(entry) > 2:
                items.append(entry)
            if len(items) >= 2:
                break
        return items or None

    def _reaction_available(self, context, contact_id, *, diagnostic=False):
        if self.semantics is None:
            return False
        return self.semantics.proactive_reaction_available(context, contact_id, diagnostic=diagnostic)

    def _proactive_image_available(self, context, job, preferences, at):
        from companion_v2.conversation.media.output_settings import effective_settings
        settings = effective_settings(self.runtime, context)
        probability = settings["proactive_probability"]
        limit = settings["proactive_max_daily"]
        if job.diagnostic_image_intent is not None:
            return True
        if probability <= 0 or limit == 0:
            return False
        if limit > 0:
            start = local_day_start(at, preferences.values["quiet_timezone"])
            # Count both sealed reaction images and judged drafts that have
            # reserved a reaction intent. Otherwise two concurrent judgements
            # can both observe zero and exceed the daily proactive-image cap.
            count = self.runtime.read_one("""SELECT COUNT(*) FROM proactive_contacts
                WHERE origin_key = ? AND created_at >= ?
                AND (json_extract(record_json, '$.reaction') IS NOT NULL
                     OR json_extract(record_json, '$.pending_reaction_intent') IS NOT NULL)""",
                (context.key, stamp(start)))[0]
            if count >= limit:
                return False
        sample = int(digest([job.request_hash, "proactive-image", probability])[:16], 16) / 0xFFFFFFFFFFFFFFFF
        return sample < probability

    def _last_plan_at(self, context, name):
        """When this relationship last sealed a voice or poke plan, if ever."""
        document_path = {"voice": "$.voice", "poke": "$.poke"}[name]
        row = self.runtime.read_one("""SELECT created_at FROM proactive_contacts
            WHERE origin_key = ? AND json_extract(record_json, ?) IS NOT NULL
            ORDER BY created_at DESC LIMIT 1""", (context.key, document_path))
        return datetime.fromisoformat(row["created_at"]) if row is not None else None

    def _planned_pokes(self, context, preferences, at):
        """How many pokes this relationship already sealed during its local day.

        A sealed plan reserves the allowance even when the wire effect later
        fails or stays unknown: the action may already have reached the
        recipient, so a retry must not exceed the configured count.
        """
        start = local_day_start(at, preferences.values["quiet_timezone"])
        return self.runtime.read_one("""SELECT COUNT(*) FROM proactive_contacts
            WHERE origin_key = ? AND created_at >= ? AND json_extract(record_json, '$.poke') IS NOT NULL""",
            (context.key, stamp(start)))[0]

    def _voice_available(self, context, preferences, at):
        if not preferences.values["voice_action_enabled"]:
            return False
        minutes = preferences.values["voice_action_cooldown_minutes"]
        last = self._last_plan_at(context, "voice") if minutes > 0 else None
        return last is None or at >= last + timedelta(minutes=minutes)

    def _poke_available(self, context, preferences, at):
        limit = preferences.values["poke_action_daily_limit"]
        if not preferences.values["poke_action_enabled"] or limit <= 0:
            return False
        if self._planned_pokes(context, preferences, at) >= limit:
            return False
        minutes = preferences.values["poke_action_cooldown_minutes"]
        last = self._last_plan_at(context, "poke") if minutes > 0 else None
        return last is None or at >= last + timedelta(minutes=minutes)

    def _expression_plans(self, context, preferences, body, decision, at, *, policy=None):
        """Decide what may leave, from model intent plus the relationship's own accounting.

        The model only says that a voice note or a poke would suit the moment.
        Availability, cooldown and the daily allowance are read back from sealed
        contacts here, so a fabricated, replayed or stale intent cannot exceed
        them. A body the guards cleared never carries either side effect.
        """
        if body is None or not body.strip():
            return None, None
        voice = poke = None
        if decision.get("voice_intent") is True and self._voice_available(context, preferences, at):
            policy = policy or VoiceReplyPolicy(position="after_text")
            # Foreign speech must be authored in the same model call. Never
            # silently route the Chinese display text to a foreign TTS voice.
            speech = decision.get("voice_text", body if policy.language == "zh" else "")
            speech = re.sub(r"\s+", " ",
                strip_voice_markup(strip_internal_expression_protocol(speech))).strip()
            if speech and len(speech) <= 2000:
                voice = VoicePlan(speech, provider_id=policy.provider_for_language(context.scope.kind.value),
                    timeout_seconds=policy.timeout_seconds, position=policy.position)
            else:
                logger.info("V2 proactive voice omitted: speech unavailable or too long language=%s", policy.language)
        if decision.get("poke_intent") is True and self._poke_available(context, preferences, at):
            poke = PokePlan(count=1)
        return voice, poke

    def start(self):
        row = self.runtime.read_one("SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (GROUP,))
        self.generation = self.configuration.start_consumer(GROUP, expected_generation=row["generation"] if row else 0)

    def proactive_denied(self, context, *, at=None):
        """主动权限被操作员收回了吗（只判定，不写任何状态）。

        收回 ``proactive.contact`` 之后，规划这一步不该再往下走：既省掉一次配置读取，
        也让"为什么没主动联系他"有个明确原因。真故障（绑定变了、作用域不对）不在这里
        吞掉——那些仍然照旧抛出，由上层按失败处理。
        """
        at = at or self.tasks.now()
        result = self.tasks.check_context_access(context, action=GROUP, observed_at=at)
        if result.is_success:
            return False
        return result.failure.code in REVOCABLE_ABSENCE_CODES

    def _policy(self, context, purpose, at, *, token=None, contact_id="", administrator_test=False):
        preferences = require(self.preferences.read(context, observed_at=at))
        preferences.require_contact(purpose, ignore_time_restrictions=administrator_test,
                                    ignore_master_switch=administrator_test)
        if token is not None and preferences.snapshot.resolution_token != token:
            raise TaskError("contact_preferences_changed", "prepared contact belongs to earlier preferences")
        if not administrator_test:
            self.budget.contacts(context, preferences, at, exclude_contact_id=contact_id)
        return preferences

    @staticmethod
    def task_id(context, opportunity, *, administrator_test=False):
        prefix = DIAGNOSTIC_TASK_PREFIX if administrator_test else "contact-task-"
        return prefix + digest([context.scope.key, opportunity.opportunity_id])

    @staticmethod
    def _origin_for_selected_intent(origin, purpose):
        """Keep only candidate metadata that belongs to the selected thought.

        A regular conversation slot may carry a relationship candidate alongside
        its chat candidate.  The scheduler needs the full origin while it is
        choosing, but once a thought wins, persisting the other candidate's
        marker would make later prompt assembly and delivery diagnostics describe
        a route that was never selected.
        """
        if not isinstance(origin, dict):
            return origin
        relationship = origin.get("relationship_event")
        if not isinstance(relationship, dict):
            return origin
        purposes = relationship.get("purposes")
        if isinstance(purposes, list):
            if purpose not in purposes:
                return {key: value for key, value in origin.items() if key != "relationship_event"}
            narrowed = {**relationship, "purposes": [purpose]}
            return {**origin, "relationship_event": narrowed}
        marker_purpose = relationship.get("purpose")
        if marker_purpose is not None and marker_purpose != purpose:
            return {key: value for key, value in origin.items() if key != "relationship_event"}
        return origin

    def _continuity(self, context, history, interaction, origin, at, *, last_reply=None,
                    dialogue_history=None, diagnostic_origin=None, diagnostic_role_instruction=None):
        if history is not None:
            if self.history is None:
                raise TaskError("delivery_history_unavailable", "contact requires its original history owner")
            self.history.read(history, context, at, action=GROUP)
        if last_reply is not None:
            if self.history is None:
                raise TaskError("delivery_history_unavailable", "contact requires its original reply history")
            self.history.verify_reply(last_reply, context, at)
        if dialogue_history is not None:
            if self.history is None:
                raise TaskError("delivery_history_unavailable", "contact requires its original conversation history")
            self.history.conversation.read(dialogue_history, context, at)
        if interaction is not None and self.history.interaction(context, at, action=GROUP)["latest_input"] != interaction["latest_input"]:
            raise TaskError("contact_conversation_changed", "a new or withdrawn input superseded the contact decision")
        if diagnostic_origin is not None:
            if self.schedule is None:
                raise TaskError("contact_schedule_unavailable", "diagnostic contact requires its original scheduler")
            self.schedule.validate_diagnostic_origin(context, diagnostic_origin, at,
                purpose=(diagnostic_origin.get("_diagnostic") or {}).get("purpose"),
                role_instruction=diagnostic_role_instruction)
        elif origin is not None:
            if self.schedule is None:
                raise TaskError("contact_schedule_unavailable", "automatic contact requires its original scheduler")
            self.schedule.validate_origin(context, origin, at)

    def _adopt(self, context, preferences):
        # The persisted receipt survives restart and transactional rollback;
        # an unchanged polling pass must not rewrite it every second.
        row = self.runtime.read_one("""SELECT a.state, a.resolution_token, a.generation FROM configuration_applications a
            JOIN configuration_consumers c ON c.consumer_id = a.consumer_id AND c.generation = a.generation
            WHERE a.target_key = ? AND a.group_id = ? AND a.consumer_id = ?""",
            (preferences.snapshot.target.key, GROUP, GROUP))
        if (row is not None and row["generation"] == self.generation and row["state"] == "applied"
                and row["resolution_token"] == preferences.snapshot.resolution_token):
            return
        require(self.configuration.report_application(context.actor, snapshot=preferences.snapshot,
            generation=self.generation, succeeded=True, observed_at=preferences.observed_at))

    def _records(self, context, sources, at, *, hashes=None):
        records = []
        for source in sources:
            if source.scope != context.scope:
                raise TaskError("contact_source_scope", "contact evidence belongs to another window")
            require(self.sources.validate_reference(source, observed_at=at))
            record = require(self.sources.read(source, resolved=context.actor, capability="text",
                observed_at=at, authorizer=self.tasks.identity))
            if not set(context.audience.recipients).issubset(record.audience.recipients):
                raise TaskError("source_audience_mismatch", "contact evidence is unavailable to its original audience")
            if digest(record.payload) != record.content_hash:
                raise TaskError("contact_source_changed", "contact evidence no longer matches its stored hash")
            records.append(record)
        if hashes is not None and tuple(record.content_hash for record in records) != hashes:
            raise TaskError("contact_source_changed", "contact evidence changed after selection")
        return tuple(records)

    @staticmethod
    def _dangling_after_own_reply(job, segments):
        """True when a proactive turn only continues the role's own last reply.

        ``contacts_since_last_input`` counts proactive contacts, not passive
        replies.  After the role spoke last and the user stayed silent, a new
        slot can therefore open immediately; starting it with ``哦？/那/照这么``
        reads as the role answering itself instead of opening a new turn.
        Such a message is skipped rather than sent as a dangling half-dialogue.
        """
        if not segments:
            return False
        last = job.last_reply or {}
        interaction = job.interaction or {}
        try:
            replied_at = datetime.fromisoformat(last["observed_at"])
            input_at = datetime.fromisoformat(interaction["last_input_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if replied_at <= input_at:
            return False
        openers = ("哦", "噢", "嗯", "啊", "唉", "那", "所以", "照这么", "这么说",
                   "然后", "不过", "可是", "而且", "说到", "接着", "对", "是嘛",
                   "真的", "确实", "也是", "还有")
        for part in segments[:2]:
            text = part.strip().strip("。，！？!?…~～、,.;:： ")
            if not text or len(text) > 12:
                continue
            if any(text == opener or text.startswith(opener) for opener in openers):
                return True
        return False

    def _duplicate(self, context, body, preferences, at, *, purpose, contact_id="", origin=None,
                   administrator_test=False):
        if not body or (not administrator_test and not preferences.dedup_applies(purpose, origin)) or self.history is None:
            return False
        weather = origin is not None and any(key in origin for key in
            ("weather_event", "weather_change_event", "official_alert_event"))
        minutes = preferences.values["dedup_weather_window_minutes" if weather else "dedup_sent_window_minutes"]
        normalized = " ".join(body.split())
        cutoff = stamp(at - timedelta(minutes=minutes)) if minutes else ""
        current = self.runtime.read_one("SELECT created_at FROM proactive_contacts WHERE contact_id = ?", (contact_id,))
        rows = self.runtime.read_all("""SELECT c.contact_id, c.body, c.created_at, d.*
            FROM proactive_contacts c JOIN background_deliveries d
                ON json_extract(d.request_json, '$.source_ref') = c.contact_id
            WHERE c.origin_key = ? AND c.contact_id != ? AND d.owner = ?
            AND ((d.state = 'delivered' AND d.delivered_at >= ? AND d.delivered_at <= ?)
              OR (d.state IN ('pending', 'dispatched', 'accepted', 'unknown') AND c.created_at >= ?))
            ORDER BY c.created_at DESC, c.contact_id DESC""",
            (context.key, contact_id, GROUP, cutoff, stamp(at), cutoff))
        for row in rows:
            if " ".join(row["body"].split()) != normalized:
                continue
            if row["state"] != "delivered":
                # A test may repeat confirmed delivery, but never an unresolved wire effect.
                if current is None or (row["created_at"], row["contact_id"]) < (current["created_at"], contact_id):
                    return True
                continue
            if administrator_test:
                continue
            snapshot = {"version": HISTORY_VERSION, "context_key": context.key,
                        "references": [self.history.reference(row)]}
            try:
                if self.history.read(snapshot, context, at, action=GROUP)[0]["text"] == row["body"]:
                    return True
            except TaskError:
                continue
        if administrator_test:
            return False
        if preferences.values["dedup_last_message_enabled"]:
            last = self.history.latest_reply(context, at,
                within_minutes=preferences.values["dedup_last_message_window_minutes"], action=GROUP)
            return last is not None and " ".join(last["text"].split()) == normalized
        return False

    async def contact(self, context, opportunity, intents, *, role_instruction, selection_seed,
                      automatic_origin=None, administrator_test=False, diagnostic_source_backed=False,
                      diagnostic_origin=None, diagnostic_image_intent=None):
        """Trusted producer API; no model may fabricate its cause, identity or persona."""
        if type(administrator_test) is not bool:
            raise ValueError("administrator_test must be an explicit boolean")
        if type(diagnostic_source_backed) is not bool:
            raise ValueError("diagnostic_source_backed must be an explicit boolean")
        if (diagnostic_image_intent is not None and
                (type(diagnostic_image_intent) is not str or not diagnostic_image_intent.strip()
                 or len(diagnostic_image_intent) > 2000 or not administrator_test
                 or not diagnostic_source_backed or diagnostic_origin is None)):
            raise ValueError("forced image output requires a source-backed administrator diagnostic")
        synthetic_origin = (type(diagnostic_origin) is dict
                            and type(diagnostic_origin.get("_diagnostic")) is dict
                            and diagnostic_origin["_diagnostic"].get("route_id") == "synthetic_event")
        if diagnostic_origin is not None and (not administrator_test
                or not diagnostic_source_backed and not synthetic_origin
                or type(diagnostic_origin) is not dict or automatic_origin is not None):
            raise ValueError("diagnostic origin requires a source-backed or synthetic administrator test")
        if diagnostic_origin is not None and any(
                intent.purpose != (diagnostic_origin.get("_diagnostic") or {}).get("purpose") for intent in intents):
            raise ValueError("diagnostic producer and contact purpose must match")
        if self.runner is None:
            raise ValueError("contact worker has not been attached")
        if self._closed:
            return Outcome.failed("task_runner_closed", "contact worker is closed")
        if (not isinstance(intents, tuple) or not 1 <= len(intents) <= 32
                or any(not isinstance(i, ContactIntent) or i.thought.opportunity != opportunity for i in intents)
                or len({i.thought.thought_id for i in intents}) != len(intents)):
            raise ValueError("contact opportunity requires bounded unique thoughts for this exact opportunity")
        if (not isinstance(role_instruction, str) or not role_instruction.strip() or len(role_instruction) > 65536
                or not isinstance(selection_seed, str) or not selection_seed.strip() or len(selection_seed) > 256):
            raise ValueError("contact requires its original persona and stable selection seed")
        if opportunity.scope != context.scope:
            return Outcome.failed("contact_context_mismatch", "opportunity belongs to another original window")
        task_id = self.task_id(context, opportunity, administrator_test=administrator_test)
        inputs = [context.key, opportunity, intents, role_instruction, selection_seed]
        request_hash = digest(inputs + ([automatic_origin] if automatic_origin is not None else [])
                              + ([diagnostic_origin] if diagnostic_origin is not None else [])
                              + (["diagnostic-image", diagnostic_image_intent]
                                 if diagnostic_image_intent is not None else [])
                              + (["administrator_test"] if administrator_test else []))
        try:
            self.require_running()
            if not administrator_test and self.platform_circuit.cooldown_until(
                    context, at=self.tasks.now()) is not None:
                raise TaskError("proactive_platform_cooldown",
                                "proactive contact is paused after repeated platform send refusals")
            if CONTACT_KIND not in self.gateway.router.profiles:
                raise TaskError("model_task_unregistered", "contact requires its own explicit background model profile")
            require(self.tasks.authorize_context(context, action=GROUP, observed_at=self.tasks.now()))
            initial_preferences = require(self.preferences.read(context, observed_at=self.tasks.now()))
            self._adopt(context, initial_preferences)
            invocation = None
            registered = False
            try:
                # Keep per-window quota checks and task admission atomic so
                # concurrent opportunities cannot reserve the same allowance.
                with self.runtime.transaction():
                    require(self.tasks.authorize_context(context, action=GROUP, observed_at=self.tasks.now()))
                    prior = self.runtime.read_one("SELECT task_id FROM execution_tasks WHERE task_id = ?", (task_id,))
                    if prior is not None:
                        task = require(self.tasks.read(context, task_id))
                        if task.spec.kind != CONTACT_KIND or task.spec.request_ref != request_hash:
                            raise TaskError("contact_request_conflict", "opportunity cannot change its original inputs or authority")
                        return Outcome.success(RunResult(task))
                    if self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,)):
                        raise TaskError("contact_request_retired", "original contact remains archived and cannot be regenerated")
                    at = self.tasks.now()
                    if not opportunity.opened_at <= at < opportunity.expires_at:
                        raise TaskError("opportunity_not_current", "opportunity is not currently open")
                    preferences = require(self.preferences.read(context, observed_at=at))
                    if preferences.snapshot.resolution_token != initial_preferences.snapshot.resolution_token:
                        raise TaskError("contact_preferences_changed", "contact policy changed before task admission")
                    if not administrator_test:
                        self.budget.contacts(context, preferences, at)
                    if automatic_origin is not None and automatic_origin.get("role_hash") != digest(role_instruction):
                        raise TaskError("contact_persona_changed", "automatic contact must keep its original role instruction")
                    history = (self.history.capture(context, at, action=GROUP,
                                limit=preferences.values["generation_history_limit"],
                                max_chars=preferences.values["history_max_chars"])
                               if self.history is not None and not administrator_test else None)
                    interaction = (self.history.interaction(context, at, action=GROUP)
                                   if self.history is not None and not administrator_test else None)
                    if diagnostic_origin is not None:
                        self.schedule.validate_diagnostic_origin(context, diagnostic_origin, at,
                            purpose=intents[0].purpose, role_instruction=role_instruction)
                    else:
                        self._continuity(context, history, interaction, automatic_origin, at)
                    feasible, originals, records_by_id = [], {}, {}
                    for intent in intents:
                        if preferences.reason(intent.purpose, ignore_time_restrictions=administrator_test,
                                              ignore_master_switch=administrator_test):
                            continue
                        try:
                            records = self._records(context, intent.thought.evidence.sources, at)
                        except TaskError:
                            continue
                        if intent.thought.evidence.captured_at > at:
                            continue
                        # Preference weights affect this competition only, never rights or scheduling.
                        weight = preferences.weight(intent.purpose)
                        if automatic_origin is not None:
                            prior = self.runtime.read_one("""SELECT record_json FROM proactive_contacts
                                WHERE origin_key = ? AND json_extract(record_json, '$.purpose') = ?
                                ORDER BY created_at DESC LIMIT 1""", (context.key, intent.purpose))
                            if prior is not None:
                                previous = json.loads(prior["record_json"])
                                feedback = previous.get("silence_feedback", {})
                                if (previous.get("purpose") == intent.purpose and feedback.get("confident_skip")):
                                    penalty = preferences.values["silence_feedback_penalty"]
                                    weight *= max(0.25, 1.0 - penalty)
                        thought = replace(intent.thought, weight=weight * intent.thought.weight)
                        feasible.append(thought)
                        originals[thought.thought_id], records_by_id[thought.thought_id] = intent, records
                    selection = require(ProactivePlanner().select(opportunity, tuple(feasible),
                        observed_at=at, selection_seed=selection_seed))
                    intent = originals[selection.selected.thought_id]
                    records = records_by_id[intent.thought.thought_id]
                    selected_origin = self._origin_for_selected_intent(automatic_origin, intent.purpose)
                    job = ContactJob(context, intent, role_instruction, request_hash,
                        preferences.snapshot.resolution_token, tuple(record.content_hash for record in records),
                        selection.feasible_count, selection.weight_used, history, interaction, selected_origin,
                        administrator_test=administrator_test,
                        diagnostic_source_backed=diagnostic_source_backed, diagnostic_origin=diagnostic_origin,
                        diagnostic_image_intent=diagnostic_image_intent)
                    presentation = ReplySettings(self.configuration).capture(context, at)["settings"]
                    segmentation_settings = {key: presentation[key]
                                             for key in _SEGMENTATION_SETTINGS_KEYS if key in presentation}
                    if not administrator_test:
                        state_summary = self._role_state_summary(context, at)
                        if state_summary is not None:
                            if "energy" in state_summary and "role_energy" not in segmentation_settings:
                                segmentation_settings["role_energy"] = state_summary["energy"]
                            if humanized.is_sleepy(state_summary):
                                segmentation_settings.setdefault("role_sleepy", True)
                    task_deadline = min(opportunity.expires_at, at + timedelta(days=7))
                    job = replace(job, presentation_settings={key: presentation[key] for key in TEXT_DEFAULTS},
                                  voice_policy=voice_policy({**presentation, "voice_enabled": True}),
                                  segmentation_settings=segmentation_settings,
                                  deadline_at=task_deadline)
                    if not administrator_test:
                        job = replace(job, expression_style=self._capture_expression(context, at))
                    self._pending[task_id] = job
                    invocation = asyncio.current_task()
                    self._invocations.add(invocation)
                    registered = True
                    require(self.tasks.submit(TaskSpec(task_id, context, CONTACT_KIND, request_hash, task_id,
                        opportunity.opened_at, task_deadline)))
            except BaseException:
                if registered:
                    self._pending.pop(task_id, None)
                    self._invocations.discard(invocation)
                raise
            try:
                result = await self.runner.run(context, task_id)
                if administrator_test and result.is_success and result.unwrap().failure_code:
                    return Outcome.failed(result.unwrap().failure_code,
                                          "administrator diagnostic task failed before delivery")
                if result.is_success and (result.unwrap().task is None or result.unwrap().task.state in ("created", "retry_scheduled")):
                    task = require(self.tasks.discard_queued(task_id, reason="owner_unavailable"))
                    return Outcome.success(RunResult(task))
                return result
            finally:
                self._pending.pop(task_id, None)
                self._invocations.discard(invocation)
        except (TaskError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def record_delivery_receipt(self, request, receipt):
        if request.owner != GROUP or request.context.scope.account_id is None:
            return
        rejected = receipt.status == "not_sent" and receipt.failure_code == "qq_eventchecker_1200"
        self.platform_circuit.record(request.context, at=self.tasks.now(),
            failure_code=receipt.failure_code if rejected else None,
            reset_failures=(receipt.status in ("delivered", "accepted")
                            or receipt.status == "not_sent" and not rejected))
        if receipt.status == "delivered" and receipt.delivered_at is not None:
            self._record_open_loop_receipt(request, receipt.delivered_at)

    def _record_open_loop_receipt(self, request, delivered_at):
        """Record the real send time only for a delivered open-loop follow-up."""
        row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id = ?",
                                    (request.source_ref,))
        if row is None:
            return
        try:
            origin = json.loads(row["record_json"]).get("automatic_origin") or {}
            open_loop = origin.get("open_loop_event") or {}
            memo_id = open_loop.get("memo_id")
            if type(memo_id) is not str or not memo_id:
                return
            from companion_v2.agenda.service import AgendaService
            AgendaService(self.runtime).repository.record_open_loop_followup_delivery(
                memo_id, at=delivered_at)
        except (TypeError, ValueError, KeyError):
            # Delivery accounting must never make a confirmed platform receipt fail.
            return

    @outcome
    def validate(self, spec, at):
        self.require_running()
        job = self._pending.get(spec.task_id)
        if (job is None or spec.kind != CONTACT_KIND or job.request_hash != spec.request_ref
                or job.context != spec.context or spec.not_before != job.intent.thought.opportunity.opened_at
                or spec.deadline_at != (job.deadline_at or job.intent.thought.opportunity.expires_at)):
            raise TaskError("contact_request_unavailable", "original contact inputs are no longer held")
        preferences = self._policy(job.context, job.intent.purpose, at, token=job.preference_token,
                                   contact_id="contact-" + digest(spec.task_id),
                                   administrator_test=job.administrator_test)
        self._records(job.context, job.intent.thought.evidence.sources, at, hashes=job.source_hashes)
        self._continuity(job.context, job.history, job.interaction, job.automatic_origin, at,
                         last_reply=job.last_reply, dialogue_history=job.dialogue_history,
                         diagnostic_origin=job.diagnostic_origin,
                         diagnostic_role_instruction=job.role_instruction)
        return True

    def _contact_request_fits(self, run, request):
        """Preflight the selected provider without inserting a model request.

        The gateway records ``model_requests`` before it reserves the provider, so a
        prompt that exceeds the model's input ceiling cannot be retried inside the
        same task. Resolve the same route read-only and ask each provider to reserve
        it first; only a fitting request reaches ``gateway.generate``.
        """
        plan = self.gateway.router.resolve(run, request)
        if not plan.providers:
            return True
        try:
            # Only the first route is reserved here. The fallback is a later
            # execution route with its own model override; preflighting it with
            # the primary envelope would reject a fitting primary for no reason.
            plan.providers[0].reserve(plan.envelope)
            return True
        except Exception:
            logger.debug("[CompanionV2] 主动联系提示超过模型档案 task=%s",
                         run.task.task_id, exc_info=True)
            return False

    async def _generate(self, run):
        job = self._pending[run.task.task_id]
        at = self.tasks.now()
        preferences = self._policy(job.context, job.intent.purpose, at, token=job.preference_token,
                                   administrator_test=job.administrator_test)
        records = self._records(job.context, job.intent.thought.evidence.sources, at, hashes=job.source_hashes)
        source_origin = job.automatic_origin or job.diagnostic_origin
        contact_id = "contact-" + digest(run.task.task_id)
        try:
            reactions_available = self._reaction_available(job.context, contact_id,
                diagnostic=job.diagnostic_image_intent is not None)
        except Exception:
            reactions_available = False
        if job.diagnostic_image_intent is not None and not reactions_available:
            raise TaskError("proactive_diagnostic_image_unavailable",
                            "the authorized reaction image path is disabled or paused for this contact")
        try:
            voice_available = self._voice_available(job.context, preferences, at)
            poke_available = self._poke_available(job.context, preferences, at)
        except Exception:
            voice_available = poke_available = False
        completion = None
        try:
            reply_dedup_enabled = (preferences.dedup_applies(job.intent.purpose, source_origin)
                                   and preferences.values["dedup_last_message_enabled"])
            reply_window = preferences.values["dedup_last_message_window_minutes"]
            last_reply = (self.history.latest_reply(job.context, at,
                within_minutes=reply_window, action=GROUP)
                if not job.administrator_test and self.history is not None
                and reply_dedup_enabled else None)
            if last_reply is not None:
                job = replace(job, last_reply=last_reply["reference"])
                self._pending[run.task.task_id] = job
            history = self.history.read(job.history, job.context, at, action=GROUP,
                                        round_metadata=True) if job.history is not None else ()
            # Recent dialogue is independent of deduplication: yesterday's
            # answered exchange still closes a topic this afternoon. Dialogue
            # and background sends share the configured item/character budget.
            dialogue, dialogue_snapshot = (), None
            if not job.administrator_test and self.history is not None:
                dialogue_snapshot = self.history.conversation.capture(job.context, at,
                    limit=preferences.values["generation_history_limit"],
                    max_chars=preferences.values["history_max_chars"],
                    exclude_reply_part_id=(last_reply["reference"].get("part_id", last_reply["reference"].get("observation_id"))
                                           if last_reply is not None else None))
                dialogue = self.history.conversation.read(dialogue_snapshot, job.context, at,
                                                          round_metadata=True)
                kept = tuple({**group, "kind": kind}
                             for kind, items in (("dialogue", dialogue), ("background", history))
                             for group in history_round_groups(items))
                kept = sorted(kept, key=lambda group: (group["latest_at"], group["kind"]), reverse=True)
                # Reserve the latest real user exchange within the same bounded
                # budget. A run of our own proactive sends must not crowd out the
                # user's last answer and turn an answered subject into unknown.
                anchor = next((group for group in kept if group["kind"] == "dialogue"
                    and any(dialogue[index].get("role") == "user" for index in group["indices"])), None)
                if anchor is not None:
                    kept = [anchor, *(group for group in kept if group is not anchor)]
                selected, used, rounds_used = {"dialogue": set(), "background": set()}, 0, 0
                for group in kept:
                    if rounds_used >= preferences.values["generation_history_limit"]:
                        break
                    if used + group["size"] > preferences.values["history_max_chars"]:
                        continue
                    selected[group["kind"]].update(group["indices"])
                    used += group["size"]
                    rounds_used += 1
                dialogue = tuple(item for index, item in enumerate(dialogue)
                                 if index in selected["dialogue"])
                dialogue_snapshot["references"] = [ref for index, ref in enumerate(dialogue_snapshot["references"])
                                                    if index in selected["dialogue"]]
                if job.history is not None:
                    history = tuple(item for index, item in enumerate(history)
                                    if index in selected["background"])
                    history_snapshot = {**job.history, "references": [ref for index, ref in enumerate(job.history["references"])
                                                       if index in selected["background"]]}
                else:
                    history_snapshot = None
                job = replace(job, history=history_snapshot, dialogue_history=dialogue_snapshot)
                self._pending[run.task.task_id] = job
            presentation = text_policy(job.presentation_settings or {})
            segmentation = _segmentation_policy(job.segmentation_settings)
            memory_materials = () if job.administrator_test else self._memory_materials(job, records)
            expression_materials = self._expression_materials(job, contact_id)
            rhythm_snapshot = (None if job.administrator_test else
                               self.budget.rhythm_snapshot(job.context, preferences, at))
            role_state = (None if job.administrator_test else role_state_for_prompt(
                self._role_state_summary(job.context, at), job.intent.purpose))
            recent_life = (None if job.administrator_test
                           or (source_origin and any(k in source_origin for k in ("activity_event", "journal_event")))
                           else self._recent_life_summary(job.context, at))
            request = request_for(job, records, preferences, history,
                last_reply=last_reply, dialogue=dialogue,
                reactions_available=reactions_available,
                voice_available=voice_available, poke_available=poke_available,
                memory=memory_materials, expression=expression_materials,
                rhythm_snapshot=rhythm_snapshot, role_state=role_state, recent_life=recent_life)
            extra_instruction = "\n".join(text for text in
                (segmentation.instruction, presentation.instruction,
                 news_presentation_instruction(segmentation) if job.intent.purpose == "news"
                 or source_origin is not None and "news_event" in source_origin else "") if text)
            if extra_instruction:
                request = replace(request, instruction=request.instruction + "\n" + extra_instruction)
            if not self._contact_request_fits(run, request):
                # Optional memory and history are the first things a smaller
                # context model must lose; the current event, source evidence
                # and the role instruction remain authoritative.
                compact = request_for(job, records, preferences, (),
                    last_reply=None, dialogue=(),
                    reactions_available=reactions_available,
                    voice_available=voice_available, poke_available=poke_available,
                    memory=(), expression=(), rhythm_snapshot=None,
                    role_state=None, recent_life=None)
                if extra_instruction:
                    compact = replace(compact, instruction=compact.instruction + "\n" + extra_instruction)
                if not self._contact_request_fits(run, compact):
                    raise TaskError(
                        "contact_model_profile_exceeded",
                        "the proactive contact prompt exceeds the selected model input or output profile")
                logger.warning("[CompanionV2] 主动联系上下文超过模型档案，已省略可选记忆/历史 task=%s",
                               run.task.task_id)
                request = compact
            completion = await self.gateway.generate(run, request)
            if completion.draft is None:
                raise ValueError("contact generation requires a final decision")
            alert_records = ([record for record in records if record.reference.owner == OFFICIAL_ALERT_SOURCE_OWNER]
                             if source_origin is not None and "official_alert_event" in source_origin else [])
            if source_origin is not None and "official_alert_event" in source_origin and len(alert_records) != 1:
                raise TaskError("contact_official_alert_invalid", "official alert attribution source is unavailable")
            alert_material = json.loads(alert_records[0].payload) if alert_records else None
            body, reaction_intent = contact_output(completion.draft.text, official_alert=alert_material)
            if body is not None and job.diagnostic_image_intent is not None:
                reaction_intent = job.diagnostic_image_intent
            if body is not None:
                body = strip_voice_markup(strip_internal_expression_protocol(body))
            decision_data = json.loads(completion.draft.text)
            model_skip_reason = decision_data.get("skip_reason") if decision_data["decision"] == "skip" else None
            decision_confidence = float(decision_data.get("decision_confidence", 1.0))
            confidence_skip = body is not None and decision_confidence < preferences.values["silence_min_confidence"]
            if confidence_skip:
                body, reaction_intent = None, None
            run.validate()
            if (body is not None and not job.administrator_test and job.expression_style is not None
                    and job.expression_style.get("style_review_enabled", True) and self.expression_review is not None
                    and expression_settings(self.runtime, job.context).style_review_enabled):
                body = await self.expression_review.review(run, request, replace(completion.draft, text=body),
                    protected_statements=(body,) if alert_material is not None else ())
                # Review output is text only; protocol markers never become speech.
                body = strip_voice_markup(strip_internal_expression_protocol(body))
                run.validate()
            presentation_reason = None
            statements = ()
            if body is not None:
                body, leaked = presentation.framework_guard(body)
                if not leaked:
                    statements = (body,) if alert_material else observed_statements(completion.draft)
                    body = presentation.replace_text(body, statements=statements)
                else:
                    presentation_reason = "framework_guard"
                if not body.strip():
                    body, reaction_intent = None, None
                    presentation_reason = "presentation_empty"
            if body is not None and source_origin is not None and "official_alert_event" in source_origin:
                attributions = alert_material["attributions"]
                if (type(attributions) is not list or not attributions
                        or any(type(item) is not str or not item for item in attributions)):
                    raise TaskError("contact_official_alert_invalid", "official alert attribution is unavailable")
                suffix = attribution_suffix(attributions)
            else:
                suffix = ""
            provenance = None
            if body is not None and source_origin is not None and "news_event" in source_origin:
                news_records = [record for record in records if record.reference.owner == NEWS_SOURCE_OWNER]
                if len(news_records) != 1:
                    raise TaskError("contact_news_unavailable", "news attribution source is unavailable")
                try:
                    provenance = news_provenance(json.loads(news_records[0].payload))
                except (TypeError, ValueError, KeyError) as error:
                    raise TaskError("contact_news_unavailable", "news attribution is incomplete") from error
            segments = ()
            if body is not None:
                segments = segmentation.parts(body)
                segments = tuple(trim_blank_edges(part)
                                 for part in presentation.clean_parts(segments, statements=statements))
                segments = tuple(part for part in segments if part.strip())
                if not segments:
                    body, reaction_intent = None, None
                    presentation_reason = "presentation_empty"
                else:
                    if suffix:
                        segments = (*segments[:-1], segments[-1] + suffix)
                    body = "".join(segments)
            if body is not None and presentation.forbidden_word_guard(
                    body, scope_kind=job.context.scope.kind.value) is not None:
                body, reaction_intent = None, None
                segments = ()
                presentation_reason = "forbidden_word"
            dangling_reason = None
            if body is not None and self._dangling_after_own_reply(job, segments):
                body, reaction_intent = None, None
                segments = ()
                dangling_reason = "continuation_after_silence"
            dedup_reason = None
            if body is not None and self._duplicate(job.context, body, preferences, self.tasks.now(),
                    purpose=job.intent.purpose, origin=source_origin,
                    administrator_test=job.administrator_test):
                body, reaction_intent = None, None
                segments = ()
                dedup_reason = "exact_repeat"
            judge, judge_task = None, None
            if body is not None:
                judge, judge_task = self._judge_plan(job, body, preferences, self.tasks.now())
                if judge is not None and not judge.allowed and not judge.pending:
                    body, reaction_intent = None, None
                    segments = ()
                    presentation_reason = "persona_judge"
            skip_reason = None
            if body is None:
                if dangling_reason:
                    skip_reason = dangling_reason
                elif dedup_reason:
                    skip_reason = "duplicate"
                elif confidence_skip:
                    skip_reason = "low_confidence"
                elif presentation_reason == "persona_judge":
                    skip_reason = "persona_review"
                elif presentation_reason:
                    skip_reason = presentation_reason
                elif decision_data["decision"] == "skip":
                    skip_reason = "model_decision"
                elif alert_material is not None:
                    skip_reason = "source_coverage"
                else:
                    skip_reason = "no_sendable_content"
            offered = None
            deferred_reaction_intent = None
            # An image offer publishes original bytes, so it is never chosen before a
            # pending judgement: the judged payload stays body-only until it is allowed,
            # and the chosen reaction_intent is resolved once the judge approves it.
            if (body is not None and reaction_intent and reactions_available
                    and self._proactive_image_available(job.context, job, preferences, self.tasks.now())):
                if judge_task is not None:
                    deferred_reaction_intent = reaction_intent
                else:
                    try:
                        if self._reaction_available(job.context, contact_id,
                                diagnostic=job.diagnostic_image_intent is not None):
                            offered = await self.semantics.reactions.select_for_contact(job.context, reaction_intent)
                    except (TaskError, ValueError, OSError):
                        offered = None
            if body is not None and job.diagnostic_image_intent is not None and offered is None and deferred_reaction_intent is None:
                raise TaskError("proactive_diagnostic_image_unavailable",
                                "no authorized reaction image matched the diagnostic intent")
            run.validate()
            operation = OperationRef("save-" + contact_id, GROUP, job.context.scope, VersionRef(contact_id, 1), contact_id)
            document = {"decision": "skip" if body is None else
                            "judge_pending" if judge_task is not None else "send",
                "purpose": job.intent.purpose, "preference_token": job.preference_token,
                "sources": job.intent.thought.evidence.sources, "source_hashes": job.source_hashes,
                "administrator_test": job.administrator_test,
                "diagnostic_image": job.diagnostic_image_intent is not None,
                "opened_at": job.intent.thought.opportunity.opened_at,
                "expires_at": job.deadline_at or job.intent.thought.opportunity.expires_at,
                "thought_id": job.intent.thought.thought_id, "feasible_count": job.feasible_count,
                "thought_summary": job.intent.thought.summary,
                "weight_used": job.weight_used, "call_id": completion.call_id,
                "history": job.history, "interaction": job.interaction, "automatic_origin": job.automatic_origin,
                "dialogue_history": job.dialogue_history,
                "diagnostic_origin": job.diagnostic_origin,
                "diagnostic_role_instruction": (job.role_instruction if job.diagnostic_origin is not None
                    and (job.diagnostic_origin.get("_diagnostic") or {}).get("source_mode") == "no_conversation"
                    else None),
                "last_reply": job.last_reply, "dedup_reason": dedup_reason,
                "skip_reason": skip_reason, "model_skip_reason": model_skip_reason,
                "decision_confidence": decision_confidence,
                "silence_threshold": preferences.values["silence_min_confidence"],
                "judge": judge.document() if judge is not None else None,
                "silence_feedback": {"confident_skip": decision_data["decision"] == "skip"
                    and decision_confidence >= preferences.values["silence_min_confidence"]},
                "silence_reason": "below_confidence_threshold" if confidence_skip else None,
                "presentation_settings": job.presentation_settings,
                "segmentation_settings": job.segmentation_settings,
                "voice_language": job.voice_policy.language if job.voice_policy is not None else "zh",
                "expression_style": job.expression_style,
                "presentation_reason": presentation_reason}
            if provenance is not None:
                document["news_provenance"] = provenance
                document["request_hash"] = job.request_hash
            with self.runtime.transaction() as connection:
                run.validate()
                if body is not None and self._duplicate(job.context, body, preferences, self.tasks.now(),
                        purpose=job.intent.purpose, origin=source_origin,
                        administrator_test=job.administrator_test):
                    body, offered = None, None
                    segments = ()
                    document["decision"] = "skip"
                    document["dedup_reason"] = "exact_repeat"
                    document["skip_reason"] = "duplicate"
                reaction = None
                if offered is not None:
                    try:
                        if self._reaction_available(job.context, contact_id,
                                diagnostic=job.diagnostic_image_intent is not None):
                            with self.runtime.transaction():
                                issued_at = self.tasks.now()
                                image = self.semantics.reactions.contact_image(job.context, contact_id, offered, issued_at,
                                    publish=True, evidence=job.intent.thought.evidence.sources)
                                reaction = {**offered, "intent": reaction_intent, "issued_at": issued_at,
                                            "manifest": image_manifest(image)}
                    except (TaskError, ValueError, OSError):
                        reaction = None
                if job.diagnostic_image_intent is not None and reaction is None:
                    raise TaskError("proactive_diagnostic_image_unavailable",
                                    "the authorized image changed or could not be sealed for delivery")
                voice, poke = self._expression_plans(job.context, preferences, body, decision_data, self.tasks.now(),
                                                     policy=job.voice_policy)
                payload_hash = manifest_hash(body or "", (reaction["manifest"],) if reaction else ())
                part_documents = []
                if body is not None:
                    if not segments:
                        segments = (body,)
                    for index, text in enumerate(segments):
                        images = (reaction["manifest"],) if reaction is not None and index == len(segments) - 1 else ()
                        part_documents.append({"text": text, "payload_hash": manifest_hash(text, images)})
                document["parts"] = part_documents
                if deferred_reaction_intent is not None:
                    document["pending_reaction_intent"] = deferred_reaction_intent
                if reaction is not None:
                    document["reaction"] = reaction
                if voice is not None:
                    document["voice"] = voice.document()
                if poke is not None:
                    document["poke"] = poke.document()
                run.validate()
                run.begin_effect()
                parameters = [job.request_hash, payload_hash]
                if provenance is not None:
                    parameters.extend((provenance, document["sources"], document["source_hashes"]))
                require(self.operations.reserve(operation, parameter_hash=digest(parameters), observed_at=self.tasks.now()))
                connection.execute("INSERT INTO proactive_contacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (contact_id, run.task.task_id, job.context.key, encode(document), body or "", payload_hash,
                     operation.operation_id, stamp(self.tasks.now())))
                if body is None and judge_task is None and isinstance(job.automatic_origin, dict):
                    open_loop = job.automatic_origin.get("open_loop_event")
                    if isinstance(open_loop, dict) and isinstance(open_loop.get("memo_id"), str):
                        connection.execute("""UPDATE agenda_open_loop_activity
                            SET followup_count = MAX(0, followup_count - 1)
                            WHERE memo_id = ?""", (open_loop["memo_id"],))
                require(self.operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=self.tasks.now()))
                if judge_task is not None:
                    # The sealed draft is committed first; the judgement runs as its own
                    # background task and is the only path that may queue this delivery.
                    require(self.tasks.submit(TaskSpec(judge_task["task_id"], job.context, JUDGE_KIND,
                        judge_task["request_hash"], contact_id, self.tasks.now(), judge_task["deadline"])))
                elif body is not None:
                    require(self.delivery.queue(self._part_request(contact_id, job.context, document, 0,
                        job.intent.thought.opportunity.opened_at)))
                run.record_operation(operation.operation_id)
            self.notify()
            # ``body is not None and judge_task is None`` 恰好就是「事务里刚把这条消息
            # 排进投递」：待判定的分支只提交草稿，而重复检查一旦命中就把 body 置空。
            # 推送放在事务之外，不在 SQLite 写事务里 await 别家插件的写入。
            if body is not None and judge_task is None:
                await self._push_contact(job.context, contact_id, body, job.intent.purpose)
            return TaskResult("succeeded", effect="committed", result_ref=contact_id if body is not None else "contact-skipped:" + contact_id,
                              operation_id=operation.operation_id, receipt_ref=operation.operation_id)
        except BaseException:
            if completion is not None:
                self.gateway.ledger.discard(completion.call_id)
            raise

    def _part_documents(self, row, data):
        reaction = data.get("reaction")
        manifest = reaction.get("manifest") if isinstance(reaction, dict) else None
        parts = data.get("parts")
        if parts is None:
            parts = [{"text": row["body"], "payload_hash": row["payload_hash"]}]
        if (type(parts) is not list or not 1 <= len(parts) <= 64
                or any(type(part) is not dict or set(part) != {"text", "payload_hash"}
                       or type(part["text"]) is not str or type(part["payload_hash"]) is not str
                       for part in parts)
                or "".join(part["text"] for part in parts) != row["body"]):
            raise TaskError("contact_draft_unavailable", "original segmented contact text changed")
        for index, part in enumerate(parts):
            images = (manifest,) if manifest is not None and index == len(parts) - 1 else ()
            if manifest_hash(part["text"], images) != part["payload_hash"]:
                raise TaskError("contact_draft_unavailable", "original contact part hash changed")
        return tuple(parts)

    def _part_request(self, contact_id, context, data, sequence, not_before):
        parts = data.get("parts")
        if parts is None:
            row = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE contact_id = ?", (contact_id,))
            if row is None or row["origin_key"] != context.key:
                raise TaskError("contact_draft_unavailable", "original contact draft is unavailable")
            parts = self._part_documents(row, data)
        if type(sequence) is not int or not 0 <= sequence < len(parts):
            raise TaskError("contact_draft_unavailable", "original contact part sequence is invalid")
        deadline = data["expires_at"]
        if not isinstance(deadline, datetime):
            deadline = datetime.fromisoformat(deadline)
        return DeliveryRequest(GROUP, contact_id, context, contact_id, parts[sequence]["payload_hash"],
            not_before, deadline, sequence=sequence, part_count=len(parts))

    @staticmethod
    def _part_gap(data, part_text, contact_id, sequence):
        settings = data.get("segmentation_settings") or {}
        mode = settings.get("part_gap_mode", "fixed")
        if mode == "fixed":
            milliseconds = settings.get("part_gap_ms", 0)
            if type(milliseconds) is not int or not 0 <= milliseconds <= 60000:
                raise TaskError("contact_draft_unavailable", "original contact spacing changed")
        elif mode in ("random", "log"):
            lower, upper = settings.get("part_gap_min_ms", 1500), settings.get("part_gap_max_ms", 3500)
            base = settings.get("part_gap_log_base", 2.1)
            if (type(lower) is not int or type(upper) is not int or not 100 <= lower <= upper <= 60000
                    or type(base) not in (int, float) or not 1.1 <= base <= 5.0):
                raise TaskError("contact_draft_unavailable", "original contact spacing changed")
        else:
            raise TaskError("contact_draft_unavailable", "original contact spacing mode changed")
        return part_gap_seconds(settings, part_text, f"{contact_id}:{sequence}")

    def delivery_confirmed(self, delivery_id):
        """Queue one next part after a trusted receipt, or recover the same step after restart."""
        try:
            delivered = self.runtime.read_one("SELECT * FROM background_deliveries WHERE delivery_id = ?", (delivery_id,))
            if delivered is None or delivered["owner"] != GROUP or delivered["state"] != "delivered":
                return False
            original = DeliveryRequest.restore(delivered["request_json"])
            row = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE contact_id = ?", (original.source_ref,))
            if row is None or row["origin_key"] != original.context.key:
                return False
            data = json.loads(row["record_json"])
            parts = self._part_documents(row, data)
            if (original.part_count != len(parts) or original.sequence >= len(parts)
                    or original.payload_hash != parts[original.sequence]["payload_hash"]):
                raise TaskError("contact_draft_unavailable", "confirmed contact part no longer matches its draft")
            opened_at = datetime.fromisoformat(data["opened_at"])
            deadline = datetime.fromisoformat(data["expires_at"])
            if original.sequence == 0 and data.get("expression_style") is not None and self.expression_context is not None:
                try:
                    with self.runtime.transaction() as connection:
                        self.expression_context.confirm_delivery(connection, batch_id=original.source_ref,
                            context=original.context, delivered_at=datetime.fromisoformat(delivered["delivered_at"]))
                except Exception as error:
                    logger.warning("V2 proactive expression receipt unavailable: %s", type(error).__name__)
            if original.sequence == len(parts) - 1 or self.tasks.now() >= deadline:
                return False
            with self.runtime.transaction():
                previous_at, previous_text = None, ""
                for sequence in range(len(parts)):
                    probe = self._part_request(original.source_ref, original.context, data, sequence, opened_at)
                    current = self.runtime.read_one("SELECT * FROM background_deliveries WHERE delivery_id = ?",
                                                    (probe.delivery_id,))
                    if current is not None:
                        saved = DeliveryRequest.restore(current["request_json"])
                        if (saved.sequence != sequence or saved.part_count != len(parts)
                                or saved.payload_hash != parts[sequence]["payload_hash"]):
                            raise TaskError("contact_delivery_changed", "queued contact part no longer matches its draft")
                        if current["state"] != "delivered":
                            return False
                        previous_at = datetime.fromisoformat(current["delivered_at"])
                        previous_text = parts[sequence]["text"]
                        continue
                    if sequence == 0 or previous_at is None:
                        return False
                    gap = self._part_gap(data, parts[sequence]["text"] or previous_text, original.source_ref, sequence)
                    due_at = max(self.tasks.now(), previous_at + timedelta(seconds=gap))
                    if due_at >= deadline:
                        return False
                    request = self._part_request(original.source_ref, original.context, data, sequence, due_at)
                    require(self.delivery.queue(request))
                    return True
            return False
        except (TaskError, ValueError, KeyError, TypeError) as error:
            code = getattr(error, "code", type(error).__name__)
            if code != "task_queue_full":
                logger.warning("V2 proactive segmented delivery could not advance: %s", code)
            return False

    def advance_deliveries(self, *, limit=8):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("proactive delivery advancement must be bounded")
        rows = self.runtime.read_all("""SELECT d.delivery_id FROM background_deliveries d
            WHERE d.owner = ? AND d.state = 'delivered'
              AND julianday(json_extract(d.request_json, '$.deadline_at')) > julianday(?)
              AND COALESCE(json_extract(d.request_json, '$.sequence'), 0)
                    < COALESCE(json_extract(d.request_json, '$.part_count'), 1) - 1
              AND NOT EXISTS (SELECT 1 FROM background_deliveries n
                  WHERE n.owner = d.owner
                    AND json_extract(n.request_json, '$.source_ref') = json_extract(d.request_json, '$.source_ref')
                    AND COALESCE(json_extract(n.request_json, '$.sequence'), 0)
                        = COALESCE(json_extract(d.request_json, '$.sequence'), 0) + 1)
            ORDER BY d.delivered_at, d.delivery_id LIMIT ?""", (GROUP, stamp(self.tasks.now()), limit))
        for row in rows:
            self.delivery_confirmed(row["delivery_id"])
        return len(rows)

    def _judge_plan(self, job, body, preferences, at):
        """Decide whether this sealed body needs a model judgement before it may leave.

        Returns the resolved verdict, or the pending judge task the commit will
        submit. A judge that cannot run for a known reason withholds immediately;
        one that is merely outstanding leaves the draft sealed and undelivered.
        """
        values = preferences.values
        if not values["enable_llm_proactive_persona_judge"]:
            return None, None
        signature = signature_for(job.intent.purpose, body, review_context={
            "role_instruction": job.role_instruction, "preference_token": job.preference_token,
            "sources": job.intent.thought.evidence.sources, "source_hashes": job.source_hashes,
            "thought": job.intent.thought.summary,
            "dialogue_history": job.dialogue_history, "history": job.history, "last_reply": job.last_reply})
        if job.intent.purpose in exempt_purposes(preferences):
            return JudgeVerdict("send", 100, "该联系目的按配置豁免主动人格判定", signature, True), None
        cached = self.persona_judge.cached(job.context, signature, at,
            ttl_minutes=values["proactive_persona_judge_cache_minutes"],
            threshold=values["proactive_persona_judge_threshold"])
        if cached is not None:
            return cached, None
        if JUDGE_KIND not in self.gateway.router.profiles:
            return PersonaJudge.denied(signature, "judge_profile_unavailable",
                reason="主动人格判定模型档未注册，本次不发送"), None
        deadline = min(job.intent.thought.opportunity.expires_at, at + timedelta(seconds=JUDGE_TIMEOUT_SECONDS))
        if deadline <= at:
            return PersonaJudge.denied(signature, "judge_window_closed",
                reason="本次机会已无可判定时间，不发送未判定的正文"), None
        task_id = "contact-judge-" + digest([job.context.key, signature])
        verdict = PersonaJudge.pending(signature, task_id)
        return verdict, {"task_id": task_id, "signature": signature, "deadline": deadline,
                         "request_hash": digest([job.request_hash, signature])}

    @outcome
    def validate_judge(self, spec, at):
        self.require_running()
        if spec.kind != JUDGE_KIND or not spec.idempotency_key:
            raise TaskError("contact_judge_unavailable", "judgement must retain its sealed draft")
        row = self.runtime.read_one("""SELECT origin_key, record_json FROM proactive_contacts
            WHERE contact_id = ?""", (spec.idempotency_key,))
        if row is None or row["origin_key"] != spec.context.key:
            raise TaskError("contact_judge_unavailable", "judgement draft is unavailable")
        data = json.loads(row["record_json"])
        judge = data.get("judge") or {}
        if judge.get("task_id") != spec.task_id:
            raise TaskError("contact_judge_unavailable", "judgement belongs to another task")
        if data.get("decision") != "judge_pending":
            # The same running attempt must be able to finish its atomic commit.
            task = require(self.tasks.read(spec.context, spec.task_id))
            receipt = self.operations.get("save-" + spec.task_id)
            if (data.get("decision") in ("send", "skip") and judge.get("state") == "resolved"
                    and judge.get("model_call_id") and not judge.get("failure")
                    and task.state in ("running", "cancelling") and task.effect == "unknown"
                    and task.attempt_id == judge.get("attempt_id") and receipt.is_success):
                saved = receipt.unwrap()
                if (saved.state is OperationState.COMPLETED and saved.operation.owner == JUDGE_KIND
                        and saved.operation.scope == spec.context.scope
                        and saved.parameter_hash == digest([spec.request_ref, judge["signature"]])):
                    return True
            raise TaskError("contact_judge_unavailable", "draft already has a resolved judgement")
        return True

    async def _judge_generate(self, run):
        context, at = run.task.spec.context, self.tasks.now()
        contact_id = run.task.spec.idempotency_key
        row = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE contact_id = ?", (contact_id,))
        if row is None or row["origin_key"] != context.key:
            raise TaskError("contact_judge_unavailable", "judgement draft is unavailable")
        data = json.loads(row["record_json"])
        origin = data.get("automatic_origin")
        diagnostic_origin = data.get("diagnostic_origin")
        judge = data.get("judge") or {}
        if data.get("decision") != "judge_pending" or judge.get("task_id") != run.task.task_id:
            raise TaskError("contact_judge_unavailable", "draft already has a resolved judgement")
        if self.schedule is None:
            raise TaskError("contact_judge_persona_unavailable", "judgement needs the original accepted persona")
        if diagnostic_origin is not None:
            self.schedule.validate_diagnostic_origin(context, diagnostic_origin, at,
                purpose=data["purpose"], role_instruction=data.get("diagnostic_role_instruction"))
            diagnostic = diagnostic_origin["_diagnostic"]
            if diagnostic.get("source_mode") == "no_conversation":
                role_instruction = data.get("diagnostic_role_instruction")
                if not isinstance(role_instruction, str) or not role_instruction.strip():
                    raise TaskError("contact_judge_persona_unavailable",
                                    "judgement needs the original diagnostic persona")
                batch = {"role_instruction": role_instruction}
            else:
                _, batch, _ = self.schedule._origin(context, diagnostic["batch_id"], at)
        else:
            if not isinstance(origin, dict) or not origin.get("batch_id"):
                raise TaskError("contact_judge_persona_unavailable", "judgement needs the original accepted persona")
            self.schedule.validate_origin(context, origin, at)
            _, batch, _ = self.schedule._origin(context, origin["batch_id"], at)
        refs = tuple(source_from_document(item) for item in data.get("sources", ()))
        records = self._records(context, refs, at, hashes=tuple(data.get("source_hashes", ())))
        preferences = require(self.preferences.read(context, observed_at=at))
        preferences.require_contact(data["purpose"], ignore_time_restrictions=diagnostic_origin is not None,
                                    ignore_master_switch=data.get("administrator_test") is True)
        history, dialogue, last_reply = (), (), None
        if not data.get("administrator_test") and any(
                data.get(key) is not None for key in ("history", "dialogue_history", "last_reply")):
            if self.history is None:
                raise TaskError("contact_judge_history_unavailable", "judgement needs its original history owner")
            if data.get("history") is not None:
                history = self.history.read(data["history"], context, at, action=GROUP, round_metadata=True)
            if data.get("dialogue_history") is not None:
                dialogue = self.history.conversation.read(data["dialogue_history"], context, at,
                                                          round_metadata=True)
            if data.get("last_reply") is not None:
                last_reply = self.history.verify_reply(data["last_reply"], context, at)
        entry = {"context": context, "purpose": data["purpose"], "body": row["body"], "at": stamp(at),
                 "note": preferences.values["note"], "confidence": data.get("decision_confidence", 1.0),
                 "role_instruction": batch["role_instruction"], "observed_at": at,
                 "records": records, "thought": data.get("thought_summary"),
                 "cause_id": run.task.task_id, "history": history, "dialogue": dialogue, "last_reply": last_reply,
                 "role_state": role_state_for_prompt(
                     self._role_state_summary(context, at), data["purpose"])}
        completion = await self.gateway.generate(run, judge_request(entry))
        if completion.draft is None:
            raise ValueError("persona judgement requires a final decision")
        try:
            decision, score, reason = judge_output(completion.draft.text)
        except (TypeError, ValueError):
            self.gateway.ledger.discard(completion.call_id)
            raise
        threshold = preferences.values["proactive_persona_judge_threshold"]
        allowed = decision == "send" and score >= threshold
        pending_reaction_intent = data.get("pending_reaction_intent") if isinstance(data.get("pending_reaction_intent"), str) else None
        diagnostic_image = data.get("diagnostic_image") is True
        offered = None
        if allowed and pending_reaction_intent:
            try:
                if self._reaction_available(context, contact_id, diagnostic=diagnostic_image):
                    offered = await self.semantics.reactions.select_for_contact(context, pending_reaction_intent)
            except (TaskError, ValueError, OSError):
                offered = None
        if allowed and diagnostic_image and pending_reaction_intent and offered is None:
            raise TaskError("proactive_diagnostic_image_unavailable",
                            "no authorized reaction image matched the diagnostic intent")
        operation = OperationRef("save-" + run.task.task_id, JUDGE_KIND, context.scope,
                                 VersionRef(contact_id, 1), run.task.task_id)
        with self.runtime.transaction() as connection:
            run.validate()
            current = connection.execute("""SELECT body, payload_hash, record_json FROM proactive_contacts
                WHERE contact_id = ?""", (contact_id,)).fetchone()
            document = json.loads(current["record_json"])
            if (document.get("decision") != "judge_pending"
                    or (document.get("judge") or {}).get("task_id") != run.task.task_id):
                raise TaskError("contact_judge_unavailable", "draft already has a resolved judgement")
            document.pop("pending_reaction_intent", None)
            reaction = None
            if allowed and offered is not None and pending_reaction_intent:
                try:
                    if self._reaction_available(context, contact_id, diagnostic=diagnostic_image):
                        with self.runtime.transaction():
                            issued_at = self.tasks.now()
                            evidence = tuple(source_from_document(ref) for ref in document.get("sources", ()))
                            image = self.semantics.reactions.contact_image(context, contact_id, offered, issued_at,
                                publish=True, evidence=evidence)
                            reaction = {**offered, "intent": pending_reaction_intent, "issued_at": issued_at,
                                        "manifest": image_manifest(image)}
                except (TaskError, ValueError, OSError):
                    reaction = None
            if allowed and diagnostic_image and pending_reaction_intent and reaction is None:
                raise TaskError("proactive_diagnostic_image_unavailable",
                                "the authorized image changed or could not be sealed for delivery")
            payload_hash = current["payload_hash"]
            if reaction is not None:
                body_text = current["body"] or ""
                payload_hash = manifest_hash(body_text, (reaction["manifest"],))
                parts = document.get("parts") or [{"text": body_text, "payload_hash": current["payload_hash"]}]
                part_documents = []
                for index, part in enumerate(parts):
                    images = (reaction["manifest"],) if index == len(parts) - 1 else ()
                    part_documents.append({"text": part["text"], "payload_hash": manifest_hash(part["text"], images)})
                document["parts"] = part_documents
                document["reaction"] = reaction
            document["decision"] = "send" if allowed else "skip"
            document["judge"] = {**judge, "state": "resolved", "decision": decision, "score": score,
                                 "reason": reason, "allowed": allowed, "cached": False,
                                 "attempt_id": run.claim.attempt_id,
                                 "model_call_id": completion.call_id, "failure": None}
            document["skip_reason"] = None if allowed else "persona_review"
            run.begin_effect()
            require(self.operations.reserve(operation,
                parameter_hash=digest([run.task.spec.request_ref, judge["signature"]]), observed_at=self.tasks.now()))
            connection.execute("UPDATE proactive_contacts SET record_json = ?, payload_hash = ? WHERE contact_id = ?",
                               (encode(document), payload_hash, contact_id))
            if not allowed and isinstance(origin, dict):
                open_loop = origin.get("open_loop_event")
                if isinstance(open_loop, dict) and isinstance(open_loop.get("memo_id"), str):
                    connection.execute("""UPDATE agenda_open_loop_activity
                        SET followup_count = MAX(0, followup_count - 1)
                        WHERE memo_id = ?""", (open_loop["memo_id"],))
            if allowed:
                require(self.delivery.queue(self._part_request(contact_id, context, document, 0,
                    datetime.fromisoformat(document["opened_at"]))))
            require(self.operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=self.tasks.now()))
            run.record_operation(operation.operation_id)
        self.notify()
        if allowed:
            # 判定放行与直接发送是同一件事的两条互斥分支：草稿在此之前一直压着不发，
            # 现在它才真的排进投递，因此也在这里才写进记忆。反过来，把未获准的草稿
            # 提前推过去，会让牢插件记得一句角色从没说过的话。
            await self._push_contact(context, contact_id, row["body"], data.get("purpose"))
        return TaskResult("succeeded", effect="committed", result_ref=contact_id,
                          operation_id=operation.operation_id, receipt_ref=operation.operation_id)

    async def _push_contact(self, context, contact_id, body, purpose):
        """把刚排进投递的主动消息推给对外表达记忆的一方；失败不影响这次投递。

        身份取自 ``contact_id``（它本来就是 ``digest(task_id)`` 的持久身份）：同一条
        草稿无论被重放几次，牢插件看到的都是同一个 ``memory_id``。
        """

        scope = context.scope
        record = MemoryPushRecord(
            memory_id=push_memory_id(PUSH_PROACTIVE_MESSAGE, "proactive", contact_id),
            content=f"Bot 主动联系对方时说：{body}",
            kind=PUSH_PROACTIVE_MESSAGE,
            scope=_push_scope(scope.kind),
            # V2 的核心没有平台会话 id，只有 ScopeRef；用 V2 自己的稳定命名空间，
            # 并且带上这一轮的原生会话，避免不同窗口的主动消息互相覆盖。
            session_id="private_companion_v2:proactive:" + (scope.native_scope_id or scope.conversation_id or "role"),
            platform=scope.platform,
            occurred_at=self.tasks.now().isoformat(),
            tags=("proactive", "proactive_message", "bot_action"),
            metadata=(
                ("event_type", "proactive_message"),
                ("action_label", "主动私聊"),
                ("purpose", purpose or ""),
                ("contact_id", contact_id),
                # 牢插件用这段正文写它自己的可见时间线，必须是真正发出去的那句话。
                ("clean_visible_text", body),
                ("query_anchors", ["主动消息", "主动私聊", "刚才主动说了什么", "最近主动联系"]),
            ),
            confidence=0.92,
            importance=0.58,
            context=context,
        )
        await push_memory(self.memory_push, record)

    def outstanding_judgements(self, *, limit=8):
        """Sealed drafts waiting for their judgement, oldest first."""
        return self.runtime.read_all("""SELECT c.contact_id, c.origin_key, t.task_id AS judge_task_id,
              t.state AS judge_state, t.record_json AS judge_record
            FROM proactive_contacts c LEFT JOIN execution_tasks t
              ON t.task_id = json_extract(c.record_json, '$.judge.task_id')
            WHERE json_extract(c.record_json, '$.decision') = 'judge_pending'
            ORDER BY c.created_at, c.contact_id LIMIT ?""", (limit,))

    @outcome
    def delivery_text(self, request, at):
        row, data, voice, poke, parts = self._draft(request, at)
        opened_at = datetime.fromisoformat(data["opened_at"])
        if (request.deadline_at != datetime.fromisoformat(data["expires_at"])
                or request.not_before < opened_at or request.not_before >= request.deadline_at
                or at >= request.deadline_at):
            raise TaskError("opportunity_not_current", "delivery is outside its original opportunity")
        if request.sequence == 0:
            if request.not_before != datetime.fromisoformat(data["opened_at"]):
                raise TaskError("contact_delivery_changed", "first contact part must retain its original opening time")
        else:
            previous_request = self._part_request(row["contact_id"], request.context, data,
                request.sequence - 1, datetime.fromisoformat(data["opened_at"]))
            previous = self.runtime.read_one("SELECT * FROM background_deliveries WHERE delivery_id = ?",
                                             (previous_request.delivery_id,))
            if previous is None or previous["state"] != "delivered":
                raise TaskError("contact_previous_part_unconfirmed", "a previous contact part has no delivery confirmation")
            gap = self._part_gap(data, parts[request.sequence]["text"] or parts[request.sequence - 1]["text"],
                                 row["contact_id"], request.sequence)
            earliest = datetime.fromisoformat(previous["delivered_at"]) + timedelta(seconds=gap)
            if request.not_before < earliest:
                raise TaskError("contact_part_spacing_changed", "contact part spacing is earlier than its sealed policy")
        administrator_test = data.get("administrator_test") is True
        preferences = self._policy(request.context, data["purpose"], at,
                                   token=data["preference_token"], contact_id=row["contact_id"],
                                   administrator_test=administrator_test)
        self._continuity(request.context, data.get("history"), data.get("interaction"), data.get("automatic_origin"), at,
                         last_reply=data.get("last_reply"), dialogue_history=data.get("dialogue_history"),
                         diagnostic_origin=data.get("diagnostic_origin"),
                         diagnostic_role_instruction=data.get("diagnostic_role_instruction"))
        if data.get("diagnostic_origin") is not None:
            refs = tuple(source_from_document(item) for item in data.get("sources", ()))
            self._records(request.context, refs, at, hashes=tuple(data.get("source_hashes", ())))
        presentation = text_policy(data.get("presentation_settings") or {})
        if presentation.forbidden_word_guard(row["body"], scope_kind=request.context.scope.kind.value) is not None:
            raise TaskError("forbidden_word_blocked", "contact contains a configured forbidden word")
        if self._duplicate(request.context, row["body"], preferences, at,
                purpose=data["purpose"], contact_id=row["contact_id"],
                origin=data.get("automatic_origin") or data.get("diagnostic_origin"),
                administrator_test=administrator_test):
            raise TaskError("contact_duplicate", "another confirmed or unresolved contact already uses this text")
        part_text = parts[request.sequence]["text"]
        final_part = request.sequence == len(parts) - 1
        # Delivery re-reads the sealed plan only. It never re-decides presentation,
        # so a restart sends exactly what the original decision sealed.
        reaction = data.get("reaction")
        if reaction is None:
            if not final_part or (voice is None and poke is None):
                return part_text
            return DeliveryContent(part_text, (), voice=voice, poke=poke)
        if not self._reaction_available(request.context, row["contact_id"],
                diagnostic=data.get("diagnostic_image") is True):
            raise TaskError("reaction_unavailable", "selected proactive reaction is no longer allowed")
        image = self.semantics.reactions.contact_image(request.context, row["contact_id"], reaction,
            datetime.fromisoformat(reaction["issued_at"]), evidence=tuple(source_from_document(ref) for ref in data["sources"]))
        if image_manifest(image) != reaction["manifest"]:
            raise TaskError("reaction_changed", "selected proactive reaction no longer matches its original")
        if not final_part:
            return part_text
        return DeliveryContent(part_text, (image,), voice=voice, poke=poke)

    def _draft(self, request, at, *, historical=False):
        require(self.tasks.authorize_context(request.context, action=GROUP, observed_at=at))
        row = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE contact_id = ?", (request.source_ref,))
        if (row is None or request.owner != GROUP or request.logical_key != row["contact_id"]
                or row["origin_key"] != request.context.key):
            raise TaskError("contact_draft_unavailable", "delivery must retain its original contact draft and authority")
        data = json.loads(row["record_json"])
        try:
            # A damaged presentation plan is an unavailable draft, never a silent
            # downgrade to text: the recipient either gets the sealed plan or nothing.
            voice, poke = VoicePlan.parse(data.get("voice")), PokePlan.parse(data.get("poke"))
        except ValueError as error:
            raise TaskError("contact_draft_unavailable", "original contact voice or poke plan changed") from error
        reaction = data.get("reaction")
        if ((reaction is not None and (not isinstance(reaction, dict) or not isinstance(reaction.get("manifest"), dict)))
                or manifest_hash(row["body"], (reaction["manifest"],) if reaction else ()) != row["payload_hash"]):
            raise TaskError("contact_draft_unavailable", "original contact image manifest or body changed")
        if data["decision"] != "send":
            raise TaskError("contact_not_selected", "a skipped contact has no deliverable body")
        parts = self._part_documents(row, data)
        if (request.part_count != len(parts) or request.sequence >= len(parts)
                or request.payload_hash != parts[request.sequence]["payload_hash"]):
            raise TaskError("contact_delivery_changed", "delivery must retain its original contact part")
        operation = require(self.operations.get(row["operation_id"]))
        if operation.state is not OperationState.COMPLETED:
            raise TaskError("contact_draft_unavailable", "draft publication has no completed local receipt")
        provenance = data.get("news_provenance")
        refs = tuple(source_from_document(ref) for ref in data["sources"])
        hashes = tuple(data["source_hashes"])
        if provenance is not None:
            if (data.get("purpose") != "news" or type(provenance) is not dict
                    or operation.parameter_hash != digest([data.get("request_hash"), row["payload_hash"],
                                                           provenance, data["sources"], data["source_hashes"]])):
                raise TaskError("contact_draft_unavailable", "original news provenance changed")
        if historical and provenance is not None:
            # Attribution is an archived account of what backed a confirmed
            # share, not a renewed lease to assert or send current news. Current
            # identity, original audience, content hashes and revocation apply.
            require(self.tasks.authorize_context(request.context, action="conversation.reply", observed_at=at))
            if len(refs) != len(hashes) or sum(ref.owner == NEWS_SOURCE_OWNER for ref in refs) != 1:
                raise TaskError("contact_draft_unavailable", "original news evidence changed")
            for ref, content_hash in zip(refs, hashes):
                if ref.scope != request.context.scope:
                    raise TaskError("contact_draft_unavailable", "news provenance belongs to another window")
                if ref.owner == NEWS_SOURCE_OWNER:
                    require(self.sources.validate_archived_provenance(ref,
                        recipient=request.context.actor.subject, observed_at=at,
                        expected_content_hash=content_hash))
                else:
                    self._records(request.context, (ref,), at, hashes=(content_hash,))
        else:
            self._records(request.context, refs, at, hashes=hashes)
        if data.get("last_reply") is not None:
            if self.history is None:
                raise TaskError("delivery_history_unavailable", "contact requires its original reply history")
            self.history.verify_reply(data["last_reply"], request.context, at, historical=historical)
        if data.get("dialogue_history") is not None:
            if self.history is None:
                raise TaskError("delivery_history_unavailable", "contact requires its original conversation history")
            self.history.conversation.read(data["dialogue_history"], request.context, at, historical=historical)
        return row, data, voice, poke, parts

    @outcome
    def history_text(self, request, at):
        row, data, _, _, parts = self._draft(request, at, historical=True)
        # Pauses and expired opportunities govern future sends, not the truth of
        # a past receipt. Revoked evidence and its historical ancestry still apply.
        history = data.get("history")
        reaction = data.get("reaction")
        manifests = ((reaction["manifest"],) if reaction and request.sequence == len(parts) - 1 else ())
        return HistoricalText(parts[request.sequence]["text"], tuple(history["references"]) if history else (), manifests)

    @property
    def active_handles(self):
        return ((self.runner.active_handles if self.runner else ())
                + (self.judge_runner.active_handles if self.judge_runner else ())
                + tuple("contact-invocation-" + str(id(t)) for t in self._invocations))

    def _delivery_row(self, task_id):
        rows = self._delivery_rows(task_id)
        return rows[-1] if rows else None

    def _delivery_rows(self, task_id):
        contact = self.runtime.read_one("SELECT contact_id FROM proactive_contacts WHERE task_id = ?", (task_id,))
        if contact is None:
            return ()
        return tuple(self.runtime.read_all("""SELECT * FROM background_deliveries
            WHERE owner = ? AND json_extract(request_json, '$.source_ref') = ?
            ORDER BY CAST(json_extract(request_json, '$.sequence') AS INTEGER), delivery_id""",
            (GROUP, contact["contact_id"])))

    @outcome
    def observe(self, context, task_id):
        """Authorized status only: a saved draft or platform acceptance is not delivery."""
        with self.runtime.read_snapshot():
            task = require(self.tasks.read(context, task_id))
            if task.spec.kind != CONTACT_KIND:
                raise TaskError("contact_task_required", "task does not belong to the contact worker")
            deliveries = self._delivery_rows(task_id)
            delivery = deliveries[-1] if deliveries else None
            contact = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task_id,))
            data = json.loads(contact["record_json"]) if contact is not None else {}
            judge = data.get("judge") or {}
            skip_reason = data.get("skip_reason")
            if data.get("decision") == "skip" and skip_reason is None:
                skip_reason = ("duplicate" if data.get("dedup_reason") else
                    "low_confidence" if data.get("silence_reason") else
                    "persona_review" if judge.get("allowed") is False else
                    ("persona_review" if data.get("presentation_reason") == "persona_judge"
                     else data.get("presentation_reason")) if data.get("presentation_reason") else
                    "model_decision" if data.get("decision") == "skip" else "unknown")
            expected_parts = len(data.get("parts") or ())
            states = [item["state"] for item in deliveries]
            if not deliveries:
                delivery_state = None
            elif expected_parts and len(deliveries) < expected_parts and states[-1] == "delivered":
                deadline = data.get("expires_at")
                expired = deadline is not None and self.tasks.now() >= (
                    deadline if isinstance(deadline, datetime) else datetime.fromisoformat(deadline))
                delivery_state = "partial" if expired else "pending"
            elif all(state == "delivered" for state in states) and (not expected_parts or len(deliveries) == expected_parts):
                delivery_state = "delivered"
            elif any(state in ("dispatched", "accepted", "unknown") for state in states):
                delivery_state = next(state for state in reversed(states) if state in ("dispatched", "accepted", "unknown"))
            elif "pending" in states:
                delivery_state = "pending"
            elif "failed" in states:
                delivery_state = "failed"
            else:
                delivery_state = "cancelled"
            model_task_ids = [task_id]
            judge_task_id = judge.get("task_id")
            if isinstance(judge_task_id, str) and judge_task_id and judge_task_id not in model_task_ids:
                model_task_ids.append(judge_task_id)
            placeholders = ",".join("?" for _ in model_task_ids)
            model_rows = self.runtime.read_all(f"""SELECT state, actual_tokens, actual_cost,
                    reserved_tokens, reserved_cost, latency_ms, receipt_ref FROM model_calls
                WHERE task_id IN ({placeholders})""", tuple(model_task_ids))
            provider_calls = sum(row["state"] in ("dispatched", "generated", "unknown", "discarded")
                                 for row in model_rows)
            confirmed_calls = sum(row["actual_tokens"] is not None for row in model_rows)
            latency_values = [row["latency_ms"] for row in model_rows if row["latency_ms"] is not None]
            outstanding = tuple(row for row in model_rows
                                if row["actual_tokens"] is None and row["state"] in ("reserved", "dispatched", "unknown")
                                and not is_released(row["receipt_ref"]))
            model_metrics = {"provider_calls": provider_calls,
                "pending_reservations": sum(row["state"] == "reserved" for row in model_rows),
                "unknown_calls": sum(row["state"] == "unknown" for row in model_rows),
                "confirmed_calls": confirmed_calls,
                "tokens": sum(row["actual_tokens"] or 0 for row in model_rows),
                "cost_microusd": (sum(row["actual_cost"] or 0 for row in model_rows)
                    if any(row["actual_cost"] is not None for row in model_rows) else None),
                "held_tokens": sum(row["reserved_tokens"] for row in outstanding),
                "held_cost_microusd": sum(row["reserved_cost"] for row in outstanding),
                "avg_latency_ms": round(sum(latency_values) / len(latency_values)) if latency_values else None}
            delivery_metrics = {"expected_parts": expected_parts, "created_parts": len(deliveries),
                "delivered_parts": sum(state == "delivered" for state in states),
                "pending_parts": sum(state == "pending" for state in states),
                "unresolved_parts": sum(state in ("dispatched", "accepted", "unknown") for state in states),
                "failed_parts": sum(state in ("failed", "cancelled", "expired", "dead_letter") for state in states)}
            return {"task_id": task_id, "revision": task.revision, "generation": task.state,
                "skipped": data.get("decision") == "skip", "skip_reason": skip_reason,
                "model_skip_reason": data.get("model_skip_reason"), "dedup_reason": data.get("dedup_reason"),
                "presentation_reason": data.get("presentation_reason"),
                "decision_confidence": data.get("decision_confidence"),
                "silence_threshold": data.get("silence_threshold"),
                "judge": judge.get("state"), "judge_allowed": judge.get("allowed"),
                "judge_reason": judge.get("failure") or judge.get("reason"),
                "delivery_id": delivery["delivery_id"] if delivery else None,
                "delivery": delivery_state,
                "receipt_ref": delivery["receipt_ref"] if delivery else None,
                "delivery_parts": [{"sequence": DeliveryRequest.restore(item["request_json"]).sequence,
                    "delivery_id": item["delivery_id"], "state": item["state"],
                    "receipt_ref": item["receipt_ref"]} for item in deliveries],
                "stage_metrics": {"model": model_metrics,
                    "persona_review": {"state": judge.get("state", "not_required"),
                        "allowed": judge.get("allowed")},
                    "image_attachment": {"state": "passed" if data.get("reaction") else
                                         "failed" if data.get("diagnostic_image") else "not_required",
                                         "attached": bool(data.get("reaction"))},
                    "media": {"reaction": "attached" if data.get("reaction") else
                                         "pending_review" if data.get("pending_reaction_intent") else "not_selected",
                              "voice": "sealed" if data.get("voice") else "not_selected",
                              "poke": "sealed" if data.get("poke") else "not_selected"},
                    "delivery": delivery_metrics}}

    @outcome
    def cancel(self, context, task_id, *, expected_revision):
        self.require_running()
        with self.runtime.transaction():
            task = require(self.tasks.read(context, task_id))
            if task.spec.kind != CONTACT_KIND:
                raise TaskError("contact_task_required", "task does not belong to the contact worker")
            require(self.tasks.cancel(context, task_id, expected_revision=expected_revision))
            deliveries = self._delivery_rows(task_id)
            for delivery in deliveries:
                if delivery["state"] != "pending":
                    continue
                sending = require(self.tasks.read(context, delivery["task_id"]))
                require(self.tasks.cancel(context, sending.task_id, expected_revision=sending.revision))
            if deliveries:
                self.delivery.settle()
        self.notify()
        return require(self.observe(context, task_id))

    def cancel_running(self):
        for task in tuple(self._invocations):
            task.cancel()

    async def close(self):
        self._closed = True
        self.cancel_running()
        if self.runner is not None:
            await self.runner.close()
        if self.judge_runner is not None:
            await self.judge_runner.close()
        if self._invocations:
            await asyncio.wait(tuple(self._invocations), timeout=0.25)
