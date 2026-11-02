"""Durable conversation input batches. Bodies remain under source-owner governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import json
import logging
from uuid import uuid4

from companion_v2.execution.models import TaskContext, TaskError, TaskSpec, digest, encode, require, stamp
from companion_v2.execution.service import TaskSupervisor, outcome
from companion_v2.agenda.planning_config import configured_annual_calendars
from companion_v2.proactive.configuration import GROUP as CONTACT_CONFIGURATION_GROUP
from companion_v2.proactive.configuration import target_for as contact_configuration_target_for
from companion_v2.proactive.date_context import INSTRUCTION as IMPORTANT_DATES_INSTRUCTION
from companion_v2.proactive.date_context import project_important_dates
from companion_v2.proactive.inspirations import INSTRUCTION as INSPIRATION_INSTRUCTION, SNAPSHOT_KEY as INSPIRATION_KEY
from companion_v2.delivery.history import INSTRUCTION as HISTORY_INSTRUCTION
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, VersionRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.identity_policy.personas import ConversationRoute, RuntimeScope
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository

from .foreground import (
    ContextContribution, ContributionState, EvidencePromptCompiler, ForegroundContextAssembler, PromptMaterial,
)
from .date_context import ALMANAC_INSTRUCTION, CALENDAR_INSTRUCTION, project_local_date
from .busy_gate import BUSY_DELAY_INSTRUCTION
from .perception_settings import validate as validate_perception_settings, validate_snapshot as validate_perception_snapshot
from .reply_settings import validate as validate_reply_settings
from .models import InboundConversationEvent
from . import debounce_log
from .media.models import ImageConversationEvent, INSTRUCTION as IMAGE_INSTRUCTION
from .media.codec import image_descriptor
from .media.service import ConversationImages
from .media.reply import ConversationReplyImages
from .media.generated import GeneratedImages, INSTRUCTION as GENERATED_IMAGE_INSTRUCTION
from .media.quota import ImageQuotaGate
from .media.screen import ScreenImages, INSTRUCTION as SCREEN_INSTRUCTION
from .media.audio.events import AudioConversationEvent
from .media.audio.models import AudioDescriptor, fits_audio_budget, INSTRUCTION as AUDIO_INSTRUCTION
from .media.audio.service import ConversationAudio
from .media.video.events import VideoConversationEvent
from .media.video.models import VideoDescriptor, fits_video_budget
from .media.video.service import ConversationVideos
from .quotes.models import (QuoteConversationEvent, QuoteDescriptor, event_forwards, event_images,
                            event_audios, event_videos, INSTRUCTION as QUOTE_INSTRUCTION)
from .quotes.forward_models import INSTRUCTION as QUOTED_FORWARD_INSTRUCTION
from .quotes.service import ConversationQuotes
from .forwards.models import ForwardConversationEvent, ForwardDescriptor, fits_forwards, INSTRUCTION as FORWARD_INSTRUCTION
from .forwards.service import ConversationForwards
from .relationship_context import RELATIONSHIP_INSTRUCTION, RelationshipContext
from companion_v2.relationships.profile import ConversationRelationshipProfileContext
from companion_v2.relationships.group_network import GroupRelationshipStore
from .life_context import ConversationLifeContext, LIFE_INSTRUCTION
from .growth_context import ConversationRoleGrowthContext, ROLE_GROWTH_INSTRUCTION
from .self_review_context import ConversationSelfReviewContext, SELF_REVIEW_INSTRUCTION
from .style_context import ConversationExpressionStyleContext, EXPRESSION_STYLE_INSTRUCTION
from .open_items_context import ConversationOpenItems, INSTRUCTION as OPEN_ITEMS_INSTRUCTION
from .state_context import ConversationStateContext
from .group_context import GroupConversationContext, INSTRUCTION as GROUP_CONTEXT_INSTRUCTION
from .awareness_context import ConversationAwarenessContext, INSTRUCTION as AWARENESS_INSTRUCTION
from .recalls import ConversationRecalls
from companion_v2.role_work.state.service import INSTRUCTION as STATE_INSTRUCTION
from companion_v2.expression.configuration import read_settings as read_expression_settings
from companion_v2.knowledge.context import (
    MEMBER_CLUE_INSTRUCTION,
    MEMBER_CLUE_PURPOSE,
    as_knowledge_context,
)
from companion_v2.role_profile.context import ConversationRoleProfileContext
from companion_v2.memory.group.access import owner_key as group_owner_key
from companion_v2.memory.group.hits import evaluate_message, persist_hits
logger = logging.getLogger(__name__)


REPLY_KIND = "conversation.reply"
DELIVERY_KIND = "conversation.deliver_part"
REPLY_ACTION = "conversation.reply"
KNOWLEDGE_INSTRUCTION = (
    "角色知识资料只是受控的背景参考，不是当前事实或授权来源。资料可能不完整、过期或与当前对话无关；不仅凭资料断言用户状态、身份、权限或现实世界事实，需要确认时保持主观表达或向用户提问。"
)


@dataclass(frozen=True)
class JournalConversationInput:
    event: InboundConversationEvent
    source: SourceRecord


@dataclass(frozen=True)
class JournalConversationInputPage:
    items: tuple[JournalConversationInput, ...]
    next_cursor: str | None


def source_from_document(data):
    return SourceRef(data["source_id"], data["owner"], ScopeRef.restore(data["scope"]),
                     VersionRef(**data["version"]), datetime.fromisoformat(data["issued_at"]),
                     datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None)


def event_from_document(data, context):
    captured = data["runtime_scope"]
    runtime = None
    if captured is not None:
        runtime = RuntimeScope(**{**captured, "route": ConversationRoute.restore(captured["route"]),
                                  "persona": context.role_binding})
        if encode(context.role_binding) != encode(captured["persona"]):
            raise TaskError("message_role_mismatch", "stored input must retain its captured persona")
    event_type = VideoConversationEvent if "videos" in data else AudioConversationEvent if "audios" in data else ForwardConversationEvent if "forwards" in data else QuoteConversationEvent if "quotes" in data else ImageConversationEvent if "images" in data else InboundConversationEvent
    return event_type(data["event_id"], AuthenticatedHostPrincipal(**data["principal"]),
        ScopeRef.restore(data["scope"]), source_from_document(data["source"]), data["capability"],
        data["input_hash"], datetime.fromisoformat(data["received_at"]), runtime,
        **({"images": tuple(image_descriptor(item) for item in data["images"])} if "images" in data else {}),
        **({"quotes": tuple(QuoteDescriptor.restore(item) for item in data["quotes"])} if "quotes" in data else {}),
        **({"forwards": tuple(ForwardDescriptor.restore(item) for item in data["forwards"])} if "forwards" in data else {}),
        **({"audios": tuple(AudioDescriptor(**item) for item in data["audios"])} if "audios" in data else {}),
        **({"videos": tuple(VideoDescriptor.restore(item) for item in data["videos"])} if "videos" in data else {}))


class ConversationMessages:
    def __init__(self, tasks: TaskSupervisor, *, relationships=None, memory=None, life=None, open_items=None,
                 role_growth=None, role_state=None, knowledge=None, role_self_review=None,
                 expression_style=None, reference_materials=None, timezone_name="Asia/Shanghai", solar_term_port=None,
                 calendar=None, cross_window=None):
        if relationships is not None and relationships.runtime is not tasks.runtime:
            raise ValueError("relationship context must use the conversation's original runtime")
        if life is not None and life.tasks is not tasks:
            raise ValueError("life context must use the conversation's original supervisor")
        if open_items is not None and open_items.tasks is not tasks:
            raise ValueError("open items must use the conversation's original supervisor")
        if reference_materials is not None and reference_materials.tasks is not tasks:
            raise ValueError("reference materials must use the conversation's original supervisor")
        if role_growth is not None and role_growth.reader.tasks is not tasks:
            raise ValueError("role growth context must use the conversation's original supervisor")
        self.memory_context = memory.context if memory is not None else None
        self.tasks = tasks
        self.runtime = tasks.runtime
        self.role_profile_context = ConversationRoleProfileContext(self.runtime)
        self.timezone_name = timezone_name
        self.solar_term_port = solar_term_port
        self.sources = SourceRepository(self.runtime)
        # Knowledge material and member clues are bounded by the registered
        # knowledge configuration group; a bare retriever keeps working.
        self.knowledge = None if knowledge is None else as_knowledge_context(knowledge, runtime=self.runtime)
        self.recalls = ConversationRecalls(self)
        self.images = ConversationImages(self)
        self.generated_images = GeneratedImages(self)
        self.image_quota = ImageQuotaGate(self)
        self.screen_images = ScreenImages(self)
        self.audio = ConversationAudio(self)
        self.videos = ConversationVideos(self)
        self.reply_images = ConversationReplyImages(self)
        self.reply_pokes = False
        from .media.review import GroupImageReview
        self.image_review = GroupImageReview(self)
        self.image_fallback = None
        self.outbound_delivery = None
        self.quotes = ConversationQuotes(self)
        self.forwards = ConversationForwards(self)
        self.group_context = GroupConversationContext(self)
        from companion_v2.memory.group.service import GroupMemory
        self.group_memory = GroupMemory(self)
        from companion_v2.memory.person.service import PersonAcquaintance
        self.acquaintance = PersonAcquaintance(self.runtime, self.tasks)
        self.cross_window = cross_window
        self.cross_window_context = (ConversationAwarenessContext(cross_window)
                                     if cross_window is not None else None)
        # One group graph is shared by the member card, imported clues and the
        # short interaction rhythm.  It is deliberately independent of the
        # private relationship-memory owner.
        self.group_network = GroupRelationshipStore(self.runtime)
        # Habit evidence counting rides the same intake transaction as the
        # acquaintance card; without a memory service the feature stays absent.
        from companion_v2.memory.habits import HabitProfile
        self.habits = HabitProfile(memory) if memory is not None else None
        self.confirmed_reply_sources = None
        self.assembler = ForegroundContextAssembler()
        self.compiler = EvidencePromptCompiler()
        self.tools = None
        self.reply_settings = None
        self.perception_settings = None
        self.background_history = None
        self.proactive_inspirations = None
        self.relationship_context = RelationshipContext(relationships) if relationships is not None else None
        self.relationship_profile_context = ConversationRelationshipProfileContext(self.runtime)
        self.life_context = ConversationLifeContext(life) if life is not None else None
        self.open_items_context = ConversationOpenItems(open_items) if open_items is not None else None
        self.role_growth_context = role_growth
        self.role_self_review_context = role_self_review
        self.expression_style_context = expression_style
        self.reference_materials = reference_materials
        self.role_state_context = ConversationStateContext(role_state, calendar=calendar) if role_state is not None else None
        if memory is not None:
            memory.attach_messages(self)
            self.confirmed_reply_sources = memory.writer.access.sent_sources

    def _knowledge_contributions(self, context, events, role_instruction, at, batch_id):
        """Capture bounded knowledge material and member clues as governed sources."""
        if self.knowledge is None:
            return ()
        # Retrieval uses the current turn text and role prompt, but the provider
        # never receives the authenticated principal or a raw audience object.
        source_text = []
        for event in events:
            record = self._read_source(event.source, context, event.capability, at)
            source_text.append(record.payload)
        return self.knowledge.contributions(
            context,
            text=role_instruction + "\n" + "\n".join(source_text),
            at=at,
            batch_id=batch_id,
            sources=self.sources,
            audience=context.audience,
        )

    def authorize(self, context, at=None):
        require(self.tasks.authorize_context(context, action=REPLY_ACTION, observed_at=at or self.tasks.now()))

    def batch(self, batch_id):
        row = self.runtime.read_one("SELECT * FROM conversation_batches WHERE batch_id = ?", (batch_id,))
        if row is None:
            raise TaskError("message_batch_unavailable", "original input batch is unavailable")
        return row

    def owned(self, context, batch_id):
        self.authorize(context)
        row = self.batch(batch_id)
        if row["origin_key"] != context.key:
            raise TaskError("message_batch_unavailable", "input batch belongs to another original context")
        return row

    def _read_source(self, reference, context, capability, at):
        if reference.scope != context.scope:
            raise TaskError("message_source_scope", "input material must belong to the original scope")
        require(self.sources.validate_reference(reference, observed_at=at))
        record = require(self.sources.read(reference, resolved=context.actor, capability=capability,
                                           observed_at=at, authorizer=self.tasks.identity))
        if not set(context.audience.recipients).issubset(record.audience.recipients):
            raise TaskError("source_audience_mismatch", "material is not available to the original audience")
        return record

    def _group_peer_count(self, context, at):
        """Distinct other group speakers in a short window; no names leave this method."""
        if context.scope.kind is not ScopeKind.GROUP:
            return 0
        try:
            minutes = self.cross_window.peer_minutes(context) if self.cross_window is not None else 5
        except Exception:
            minutes = 5
        try:
            rows = self.runtime.read_all("""SELECT i.event_json FROM conversation_inputs i
                JOIN conversation_batches b ON b.batch_id = i.batch_id
                WHERE json_extract(b.context_json, '$.role_binding') = ?
                  AND json_extract(b.context_json, '$.scope.kind') = 'group'
                  AND json_extract(i.event_json, '$.received_at') >= ?
                  AND json_extract(i.event_json, '$.received_at') <= ?
                ORDER BY i.rowid DESC LIMIT 64""",
                (encode(context.role_binding), stamp(at - timedelta(minutes=minutes)), stamp(at)))
        except Exception:
            return 0
        current = context.actor.principal.native_account_id
        accounts = set()
        for row in rows:
            try:
                data = json.loads(row["event_json"])
            except (TypeError, ValueError):
                continue
            account = (data.get("principal") or {}).get("native_account_id")
            if account and account != current:
                accounts.add(account)
        return min(64, len(accounts))

    @outcome
    def accept(self, context: TaskContext, event: InboundConversationEvent, *, role_instruction: str,
               batch_id: str | None = None) -> str:
        if not isinstance(role_instruction, str) or len(role_instruction) > 65536:
            raise ValueError("role instruction must be bounded text")
        # The optional derived decisions stay outside the intake transaction so
        # their configuration reads cannot contend with admission writes.
        observers = tuple(item for item in (self.acquaintance.intake_observer(context),
                                            None if self.habits is None else self.habits.intake_observer(context))
                          if item is not None)
        cross_window_enabled = self.cross_window is not None and self.cross_window.enabled(context)
        group_peers = self._group_peer_count(context, event.received_at) if cross_window_enabled else 0
        quote_sender_ids = tuple(quote.sender_id for quote in getattr(event, "quotes", ()))
        with self.runtime.transaction() as connection:
            self.authorize(context)
            from .reset import boundary
            reset = boundary(self.runtime, context.scope)
            if reset is not None and stamp(event.received_at) <= reset["reset_at"]:
                raise TaskError("conversation_context_reset", "the captured input precedes this window's reset")
            self.recalls.check(event.scope, event.event_id, event.principal.native_account_id)
            if (event.scope != context.scope or event.principal != context.actor.principal or
                    (event.runtime_scope is not None and event.runtime_scope.persona != context.role_binding)):
                raise TaskError("message_context_mismatch", "input must retain its authenticated route and persona")
            prior = connection.execute("SELECT * FROM conversation_inputs WHERE event_key = ?", (event.scoped_key,)).fetchone()
            event_hash = digest([event, context.key, role_instruction])
            if prior is not None:
                if prior["event_hash"] != event_hash or (batch_id is not None and batch_id != prior["batch_id"]):
                    raise TaskError("message_event_conflict", "original event cannot be moved or restamped")
                if prior["withdrawn"]:
                    raise TaskError("message_withdrawn", "replayed input was withdrawn")
                return prior["batch_id"]
            record = self._read_source(event.source, context, event.capability, self.tasks.now())
            if record.content_hash != event.input_hash:
                raise TaskError("message_input_changed", "input hash must match its governed source")
            if self.expression_style_context is not None:
                connection.execute("SAVEPOINT expression_feedback_intake")
                try:
                    self.expression_style_context.consume_feedback(connection, context=context,
                        text=record.payload, received_at=event.received_at)
                    connection.execute("RELEASE SAVEPOINT expression_feedback_intake")
                except Exception as error:
                    connection.execute("ROLLBACK TO SAVEPOINT expression_feedback_intake")
                    connection.execute("RELEASE SAVEPOINT expression_feedback_intake")
                    logger.warning("V2 expression feedback intake failed: %s", type(error).__name__)
            if context.scope.kind is ScopeKind.GROUP:
                try:
                    persist_hits(connection, evaluate_message(
                        owner_key=group_owner_key(context),
                        member_id=event.principal.native_account_id,
                        message_id=event.event_id,
                        message_text=record.payload,
                        occurred_at=event.received_at,
                        direction="inbound",
                    ), created_at=self.tasks.now())
                except Exception as error:
                    # Safety evidence is additive and never rejects normal intake.
                    logger.warning("V2 inbound safety evidence persistence failed: %s", type(error).__name__)
            if batch_id is None:
                if connection.execute("SELECT COUNT(*) FROM conversation_batches").fetchone()[0] >= self.tasks.limits.retained_tasks:
                    raise TaskError("message_capacity", "input retention limit requires archival before admission")
                batch_id = "reply-" + uuid4().hex
                connection.execute("""INSERT INTO conversation_batches
                    (batch_id, origin_key, context_json, role_instruction, state, revision, created_at, contributions_json)
                    VALUES (?, ?, ?, ?, 'open', 1, ?, '[]')""",
                    (batch_id, context.key, encode(context), role_instruction, stamp(self.tasks.now())))
            row = self.owned(context, batch_id)
            if row["state"] != "open" or row["role_instruction"] != role_instruction:
                raise TaskError("message_batch_sealed", "merge only into an open batch with the same original role prompt")
            count = connection.execute("SELECT COUNT(*) FROM conversation_inputs WHERE batch_id = ?", (batch_id,)).fetchone()[0]
            if count >= 32:
                raise TaskError("message_batch_full", "input batch reached its bounded message count")
            connection.execute("INSERT INTO conversation_inputs VALUES (?, ?, ?, ?, ?, 0)",
                               (event.scoped_key, batch_id, count + 1, encode(event), event_hash))
            if observers:
                for observer in observers:
                    try:
                        observer(context, event, connection)
                    except Exception as error:
                        # Optional derived counting never rejects an admitted input.
                        logger.warning("V2 conversation observer failed: %s", type(error).__name__)
            if context.scope.kind is ScopeKind.GROUP:
                try:
                    self.group_network.observe(context, event, connection, text=record.payload)
                except Exception as error:
                    # The graph is a management projection; a damaged optional
                    # record must never reject the authenticated group input.
                    logger.warning("V2 group relationship graph update failed: %s", type(error).__name__)
            if cross_window_enabled:
                try:
                    self.cross_window.record_input(connection, context, event.received_at,
                                                   record.payload, event.scoped_key, enabled=True,
                                                   source=event.source, peer_count=group_peers,
                                                   quote_sender_ids=quote_sender_ids)
                except Exception as error:
                    # Cross-window presence is optional background.  A damaged
                    # ledger must never reject an authenticated input.
                    logger.warning("V2 cross-window presence update failed: %s", type(error).__name__)
            connection.execute("UPDATE conversation_batches SET revision = revision + 1 WHERE batch_id = ?", (batch_id,))
            return batch_id

    def _request(self, row, context, at, *, include_tools=True, historical=False):
        if not historical:
            from .reset import require_current_batch
            require_current_batch(self.runtime, row["batch_id"], context.scope)
        inputs = self.runtime.read_all("SELECT * FROM conversation_inputs WHERE batch_id = ? ORDER BY position", (row["batch_id"],))
        sealed_values = json.loads(row["contributions_json"])
        saved = sealed_values if isinstance(sealed_values, dict) else {}
        reply_snapshot = saved.get("reply_settings")
        reply_values = reply_snapshot.get("settings") if isinstance(reply_snapshot, dict) else {}
        if reply_values is None:
            reply_values = {}
        elif type(reply_values) is not dict:
            raise TaskError("reply_configuration_invalid", "sealed reply settings are invalid")
        if reply_values:
            validate_reply_settings(reply_values)
        forward_adaptation_enabled = reply_values.get("forward_message_adaptation_enabled", True)
        if type(forward_adaptation_enabled) is not bool:
            raise TaskError("reply_configuration_invalid", "forward reading setting must be boolean")
        if row["state"] == "open":
            selected = [item for item in inputs if not item["withdrawn"]]
        else:
            sealed_keys = saved["inputs"]
            selected = [item for item in inputs if item["event_key"] in sealed_keys]
            if [item["event_key"] for item in selected] != sealed_keys or any(item["withdrawn"] for item in selected):
                raise TaskError("message_withdrawn", "sealed input was withdrawn")
        if not selected:
            raise TaskError("message_batch_empty", "no active inputs remain")
        events = [event_from_document(json.loads(item["event_json"]), context) for item in selected]
        # Keep the turn's calendar stable across slow model/tool rounds. ``at``
        # remains the current time for every source and permission recheck.
        calendar_at = events[-1].received_at
        audio_descriptors = tuple(a for event in events for a in event_audios(event))
        video_descriptors = tuple(v for event in events for v in event_videos(event))
        if video_descriptors and not fits_video_budget(video_descriptors, tuple(i for event in events for i in event_images(event)), audio_descriptors):
            raise TaskError("video_turn_limit", "stored video and other media exceed the shared turn budget")
        if audio_descriptors and not fits_audio_budget(audio_descriptors, tuple(i for event in events for i in event_images(event))):
            raise TaskError("audio_turn_limit", "stored audio and visual manifests exceed the shared turn budget")
        if not fits_forwards(tuple(f for event in events for f in event_forwards(event))):
            raise TaskError("forward_turn_limit", "this turn exceeds the declared forward node or text budget")
        primary = self._read_source(events[0].source, context, events[0].capability, at)
        if primary.content_hash != events[0].input_hash:
            raise TaskError("message_input_changed", "input hash no longer matches its governed source")
        contributions = []
        for event in events[1:]:
            record = self._read_source(event.source, context, event.capability, at)
            if record.content_hash != event.input_hash:
                raise TaskError("message_input_changed", "merged input hash no longer matches its governed source")
            contributions.append(ContextContribution(event.scoped_key, record.reference.owner,
                "merged_input:" + event.scoped_key, ContributionState.READY, record.payload, record.reference, True))
        for item in saved.get("materials", []) if isinstance(saved, dict) else []:
            reference = source_from_document(item["source"]) if item["source"] else None
            state = ContributionState(item["state"])
            payload = None
            if state is ContributionState.READY:
                payload = self._read_source(reference, context, events[0].capability, at).payload
            contributions.append(ContextContribution(item["contribution_id"], item["owner"], item["purpose"],
                                                     state, payload, reference, item["required_for_assertion"]))
        if context.scope.kind is ScopeKind.GROUP:
            contributions.append(self.group_context.speaker(context, events))
        if isinstance(saved, dict) and "group_context" in saved:
            contributions.extend(self.group_context.contributions(saved["group_context"], context, row["batch_id"], at,
                                                                   historical=historical))
        if isinstance(saved, dict) and "group_memory" in saved:
            contributions.extend(self.group_memory.context.contributions(saved["group_memory"], context, row["batch_id"]))
        card = self.acquaintance.card_contribution(context, at, snapshot=saved.get("person_card"))
        if card is not None:
            contributions.append(card)
        cross_window_material = None
        if (isinstance(saved, dict) and saved.get("cross_window") is not None
                and self.cross_window_context is not None):
            try:
                cross_window_material = self.cross_window_context.contributions(
                    saved["cross_window"], context, at)
            except Exception as error:
                logger.warning("V2 cross-window context unavailable: %s", type(error).__name__)
            if cross_window_material is not None:
                contributions.append(cross_window_material)
        if context.scope.kind is ScopeKind.GROUP:
            network = self.group_network.contribution(context, events, at=at, snapshot=saved.get("group_network"))
            if network is not None:
                contributions.append(network)
        relationship_materials = ()
        if isinstance(saved, dict) and "relationship_context" in saved:
            relationship_snapshot = saved["relationship_context"]
            RelationshipContext.validate_snapshot(relationship_snapshot, context)
            if self.relationship_context is None:
                if relationship_snapshot["references"]:
                    raise TaskError("relationship_context_unavailable", "original relationship owner is unavailable")
            else:
                relationship_materials = self.relationship_context.contributions(relationship_snapshot, context, at)
                contributions.extend(relationship_materials)
        life_materials = ()
        if isinstance(saved, dict) and "life_context" in saved:
            life_snapshot = saved["life_context"]
            ConversationLifeContext.validate_snapshot(life_snapshot, context)
            if self.life_context is None:
                if life_snapshot["references"]:
                    raise TaskError("life_context_unavailable", "original life owner is unavailable")
            else:
                life_materials = self.life_context.contributions(life_snapshot, context, at)
                contributions.extend(life_materials)
        growth_materials = ()
        if isinstance(saved, dict) and "role_growth" in saved:
            growth_snapshot = saved["role_growth"]
            ConversationRoleGrowthContext.validate_snapshot(growth_snapshot, context)
            if self.role_growth_context is None:
                if growth_snapshot["references"]:
                    raise TaskError("growth_context_unavailable", "original growth owner is unavailable")
            else:
                growth_materials = self.role_growth_context.contributions(growth_snapshot, context, at)
                contributions.extend(growth_materials)
        state_materials = ()
        if isinstance(saved, dict) and "role_self_review" in saved:
            review_snapshot = saved["role_self_review"]
            ConversationSelfReviewContext.validate_snapshot(review_snapshot, context)
            if self.role_self_review_context is None:
                if review_snapshot["references"]:
                    raise TaskError("self_review_context_unavailable",
                                    "original self-review owner is unavailable")
            else:
                review_materials = self.role_self_review_context.contributions(review_snapshot, context, at)
                contributions.extend(review_materials)
        if isinstance(saved, dict) and "expression_style" in saved:
            style_snapshot = saved["expression_style"]
            if self.expression_style_context is not None:
                try:
                    ConversationExpressionStyleContext.validate_snapshot(style_snapshot, context)
                    contributions.extend(
                        self.expression_style_context.contributions(style_snapshot, context, at,
                            policy=read_expression_settings(self.runtime, context), candidate_pool=True))
                except Exception as error:
                    logger.warning("V2 expression materials unavailable: %s", type(error).__name__)
        if isinstance(saved, dict) and saved.get("role_state") is not None:
            if self.role_state_context is None:
                raise TaskError("state_owner_unavailable", "original role state owner is unavailable")
            state_materials = self.role_state_context.contributions(saved["role_state"], context, at,
                historical=historical, fallback_source=events[0].source)
            contributions.extend(state_materials)
        knowledge_materials = tuple(
            item for item in contributions
            if item.owner == "knowledge.v2" and item.state is ContributionState.READY
        )
        knowledge_clues = tuple(
            item for item in knowledge_materials if item.purpose == MEMBER_CLUE_PURPOSE
        )
        open_item_materials = ()
        if isinstance(saved, dict) and "open_items" in saved:
            open_item_snapshot = saved["open_items"]
            ConversationOpenItems.validate_snapshot(open_item_snapshot, context)
            if self.open_items_context is None:
                if open_item_snapshot["references"]:
                    raise TaskError("open_items_unavailable", "original agenda owner is unavailable")
            else:
                open_item_materials = self.open_items_context.contributions(open_item_snapshot, context, at)
                contributions.extend(open_item_materials)
        if isinstance(saved, dict) and "background_history" in saved:
            if self.background_history is None:
                raise TaskError("delivery_history_unavailable", "original background history owner is unavailable")
            history = self.background_history.read(saved["background_history"], context, at, historical=historical)
            contributions.extend(ContextContribution("sent:" + str(index), "delivery.history",
                "confirmed_background_message", ContributionState.READY, json.dumps(item, ensure_ascii=False))
                for index, item in enumerate(history))
        if INSPIRATION_KEY in saved:
            if self.proactive_inspirations is None:
                raise TaskError("inspiration_owner_unavailable", "original inspiration list owner is unavailable")
            contributions.append(self.proactive_inspirations.contribution(saved[INSPIRATION_KEY], context, at))
        if "memory_context" in saved:
            if self.memory_context is None and (saved["memory_context"].get("references")
                    or saved["memory_context"].get("experience_references")):
                raise TaskError("memory_owner_unavailable", "original memory context owner is unavailable")
            if self.memory_context is not None:
                contributions.extend(self.memory_context.contributions(saved["memory_context"], context))
        perception = saved.get("perception") if isinstance(saved, dict) else None
        if perception is not None:
            if (context.scope.kind is not ScopeKind.PRIVATE or type(perception) is not dict
                    or not {"settings", "configuration_token", "annual_calendars"} <= set(perception)
                    or type(perception["configuration_token"]) is not str
                    or type(perception["annual_calendars"]) is not str):
                raise TaskError("perception_snapshot_invalid", "sealed perception settings are invalid")
            perception_values = validate_perception_snapshot(perception["settings"])
            annual_calendars = configured_annual_calendars(perception["annual_calendars"])
        else:
            perception_values = None
            annual_calendars = None
            if context.scope.kind is ScopeKind.PRIVATE and self.role_state_context is not None:
                settings, _ = self.role_state_context.service.settings(context)
                annual_calendars = configured_annual_calendars(settings["planning_annual_calendars"])
        important_dates = saved.get("important_dates") if isinstance(saved, dict) else None
        important_date_settings = None
        if important_dates is not None:
            if (context.scope.kind is not ScopeKind.PRIVATE or type(important_dates) is not dict
                    or set(important_dates) != {"settings", "configuration_token"}
                    or type(important_dates["configuration_token"]) is not str
                    or type(important_dates["settings"]) is not dict):
                raise TaskError("important_dates_snapshot_invalid", "sealed important dates are invalid")
            important_date_settings = important_dates["settings"]
        calendar_enabled = perception_values is None or perception_values["calendar_enabled"]
        timezone_name = perception_values["calendar_timezone"] if perception_values else self.timezone_name
        if calendar_enabled:
            calendar_payload = json.dumps(project_local_date(calendar_at, timezone_name, annual_calendars,
                                          lunar_enabled=perception_values is None or perception_values["lunar_enabled"],
                                          holiday_enabled=perception_values is None or perception_values["holiday_enabled"],
                                          holiday_country=perception_values["holiday_country"] if perception_values else "CN",
                                          automatic_solar_terms_enabled=perception_values is not None and perception_values["automatic_solar_terms_enabled"],
                                          almanac_enabled=perception_values is not None and perception_values["almanac_enabled"],
                                          solar_term_port=self.solar_term_port),
                                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            contributions.append(ContextContribution(
                "calendar:" + timezone_name, "conversation.calendar", "local_calendar_context",
                ContributionState.READY, calendar_payload))
            if important_date_settings is not None:
                date_context = project_important_dates(calendar_at, important_date_settings)
                try:
                    from companion_v2.relationships import anniversaries
                    date_context["auto_anniversaries"] = list(anniversaries.upcoming(
                        self.runtime, context, calendar_at,
                        timezone_name=date_context["timezone"],
                        lookahead_days=important_date_settings["lookahead_days"]))
                except Exception:
                    date_context["auto_anniversaries"] = []
                if date_context["entries"] or date_context["auto_anniversaries"]:
                    contributions.append(ContextContribution(
                        "important-dates:" + date_context["timezone"], "conversation.calendar",
                        "upcoming_important_dates", ContributionState.READY,
                        json.dumps(date_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        images, audios, videos = [], [], []
        for event in events:
            clips = self.videos.read(context, (event,), at) if getattr(event, "videos", ()) else ()
            for video in clips:
                contributions.extend(self.videos.contributions(event, video))
            videos.extend(clips)
            voices = self.audio.read(context, (event,), at) if getattr(event, "audios", ()) else ()
            for audio in voices:
                contributions.append(self.audio.contribution(event, audio))
            audios.extend(voices)
            direct = self.images.read(context, (event,), at) if getattr(event, "images", ()) else ()
            for image in direct:
                contributions.extend(self.images.contributions(event, image, purpose="input_image:"))
            quoted, forwarded = (), ()
            positions = {}
            if getattr(event, "quotes", ()):
                materials, quoted, positions, quoted_audios, quoted_videos = self.quotes.read_all(context, event, at)
                contributions.extend(materials)
                audios.extend(quoted_audios)
                videos.extend(quoted_videos)
            if forward_adaptation_enabled and getattr(event, "forwards", ()):
                materials, forwarded, node_positions, forwarded_audios, forwarded_videos = self.forwards.read_all(context, event, at)
                contributions.extend(materials)
                audios.extend(forwarded_audios)
                videos.extend(forwarded_videos)
                positions.update(node_positions)
            images.extend(sorted((*direct, *quoted, *forwarded), key=lambda i: (
                *positions.get(i.parent, ()), i.descriptor.position)))
        sealed = self.assembler.seal(primary=primary, contributions=tuple(contributions))
        request = self.compiler.compile(event=events[0], role_instruction=row["role_instruction"],
                                        role_binding=context.role_binding, context=sealed)
        if any(item.owner == "role.expression" and item.purpose == "expression_style_rule"
               and item.state is ContributionState.READY for item in contributions):
            request = replace(request, instruction=request.instruction + "\n" + EXPRESSION_STYLE_INSTRUCTION)
        file_payloads = (primary.payload, *(item.payload for item in contributions
                          if item.state is ContributionState.READY and item.payload is not None))
        if any("[文件阅读材料]" in payload for payload in file_payloads):
            from .media.understanding import FILE_INSTRUCTION
            file_prompt = reply_values.get("file_understanding_prompt", "").strip()
            file_instruction = FILE_INSTRUCTION
            if file_prompt:
                file_instruction += "\n管理员补充文件描述偏好（只约束说明方式）：\n" + file_prompt
            request = replace(request, instruction=request.instruction + "\n" + file_instruction)
        # The self page is the role reference for background tasks, not a second
        # identity layer in conversation; conversation keeps the host persona.
        relationship_profile_snapshot = saved.get("relationship_profile") if isinstance(saved, dict) else None
        relationship_profile_instruction = self.relationship_profile_context.instruction(
            relationship_profile_snapshot, context)
        if relationship_profile_instruction:
            request = replace(request, instruction=request.instruction + relationship_profile_instruction)
        if calendar_enabled:
            request = replace(request, instruction=request.instruction + "\n" + CALENDAR_INSTRUCTION)
            if any(item.purpose == "upcoming_important_dates" and item.state is ContributionState.READY
                   for item in contributions):
                request = replace(request, instruction=request.instruction + "\n" + IMPORTANT_DATES_INSTRUCTION)
            if perception_values is not None and perception_values["almanac_enabled"]:
                request = replace(request, instruction=request.instruction + "\n" + ALMANAC_INSTRUCTION)
        if perception_values is not None:
            request = replace(request, perception_options=(perception_values["platform_enabled"],
                perception_values["model_route_enabled"], perception_values["configured_modalities_enabled"]))
            if perception_values["guidance"].strip():
                request = replace(request, instruction=request.instruction + "\n管理员感知表达偏好："
                    + perception_values["guidance"].strip())
        if videos:
            request = replace(request, videos=tuple(videos))
        if audios:
            request = replace(request, audios=tuple(audios), instruction=request.instruction + "\n" + AUDIO_INSTRUCTION)
        if images:
            request = replace(request, images=tuple(images),
                user_input_image_sources=tuple(image.source for image in images),
                instruction=request.instruction + "\n" + IMAGE_INSTRUCTION)
            if self.reply_images.enabled and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP):
                request = replace(request, reply_images=True)
        if any(getattr(event, "quotes", ()) for event in events):
            request = replace(request, instruction=request.instruction + "\n" + QUOTE_INSTRUCTION)
        if forward_adaptation_enabled and any(event_forwards(event) for event in events):
            request = replace(request, instruction=request.instruction + "\n" + FORWARD_INSTRUCTION)
        if forward_adaptation_enabled and any(getattr(q, "forwards", ()) for event in events for q in getattr(event, "quotes", ())):
            request = replace(request, instruction=request.instruction + "\n" + QUOTED_FORWARD_INSTRUCTION)
        if cross_window_material is not None:
            request = replace(request, instruction=request.instruction + "\n" + AWARENESS_INSTRUCTION)
        if any(item.state is ContributionState.READY for item in relationship_materials):
            request = replace(request, instruction=request.instruction + "\n" + RELATIONSHIP_INSTRUCTION)
        if any(item.state is ContributionState.READY for item in life_materials):
            request = replace(request, instruction=request.instruction + "\n" + LIFE_INSTRUCTION)
        if any(item.state is ContributionState.READY for item in growth_materials):
            request = replace(request, instruction=request.instruction + "\n" + ROLE_GROWTH_INSTRUCTION)
        if any(item.state is ContributionState.READY for item in open_item_materials):
            request = replace(request, instruction=request.instruction + "\n" + OPEN_ITEMS_INSTRUCTION)
        if state_materials:
            request = replace(request, instruction=request.instruction + "\n" + STATE_INSTRUCTION
                              + self.role_state_context.instruction(state_materials))
        if isinstance(saved, dict) and saved.get("busy_delay") is True:
            request = replace(request, instruction=request.instruction + "\n" + BUSY_DELAY_INSTRUCTION)
        if knowledge_materials:
            request = replace(request, instruction=request.instruction + "\n" + KNOWLEDGE_INSTRUCTION)
        if knowledge_clues:
            request = replace(request, instruction=request.instruction + "\n" + MEMBER_CLUE_INSTRUCTION)
        if isinstance(saved, dict) and "background_history" in saved:
            request = replace(request, instruction=request.instruction + "\n" + HISTORY_INSTRUCTION)
        if INSPIRATION_KEY in saved:
            request = replace(request, instruction=request.instruction + "\n" + INSPIRATION_INSTRUCTION)
        if include_tools:
            if self.tools is not None:
                request = self.tools.augment(row, context, request, at)
            elif self.runtime.read_one("SELECT 1 FROM conversation_tool_calls WHERE batch_id = ? LIMIT 1", (row["batch_id"],)):
                raise TaskError("tool_owner_unavailable", "original tool results require their registered owners")
            if (self.reference_materials is not None and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP)
                    and self.tools is not None):
                try:
                    actions = {tool.action for tool in self.tools.catalog_for(context, row["batch_id"])}
                    if "image.edit" in actions:
                        from zoneinfo import ZoneInfo
                        from companion_v2.references.prompts import image_reference_instruction
                        local_date = calendar_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
                        material = self.reference_materials.prompt_material(context, local_date=local_date)
                        instruction = image_reference_instruction(material)
                        if instruction:
                            request = replace(request, instruction=request.instruction + "\n" + instruction)
                except TaskError:
                    # A reference library is optional context; failure to read it
                    # must not stop an otherwise valid conversation turn.
                    pass
        if context.scope.kind is ScopeKind.GROUP:
            request = replace(request, instruction=request.instruction + "\n" + GROUP_CONTEXT_INSTRUCTION)
        generated_image_instruction = False
        if include_tools:
            generated = self.generated_images.read_batch(row, context, at)
            if generated:
                materials = tuple(PromptMaterial(item.owner, item.purpose, item.payload, item.source)
                    for image in generated for item in self.generated_images.contributions(image))
                request = replace(request, images=request.images + generated,
                    context=replace(request.context, materials=request.context.materials + materials),
                    reply_images=self.reply_images.enabled and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP))
                generated_image_instruction = True
        semantic = getattr(self, "semantics", None)
        if include_tools and semantic is not None:
            request = semantic.augment(row, context, request)
        if include_tools:
            screens = self.screen_images.read_batch(row, context, at, request)
            if screens:
                materials = tuple(PromptMaterial(item.owner, item.purpose, item.payload, item.source)
                    for image in screens for item in self.screen_images.contributions(image))
                request = replace(request, images=request.images + screens,
                    context=replace(request.context, materials=request.context.materials + materials),
                    instruction=request.instruction + "\n" + SCREEN_INSTRUCTION)
        if generated_image_instruction:
            request = replace(request, instruction=request.instruction + "\n" + GENERATED_IMAGE_INSTRUCTION)
        actions = ["text", "silent"]
        if request.reply_images:
            actions.append("image")
        if (reply_values.get("voice_enabled") is True
                and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP)):
            actions.append("voice_only")
        if self.reply_pokes and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            actions.append("poke")
        request = replace(request, reply_actions=tuple(actions))
        return request

    @outcome
    def seal(self, context: TaskContext, batch_id: str, *, deadline_at: datetime,
             contributions: tuple[ContextContribution, ...] = ()):
        row = self.owned(context, batch_id)
        captured_knowledge = ()
        inputs = ()
        if row["state"] == "open":
            inputs = self.runtime.read_all(
                "SELECT event_key, event_json FROM conversation_inputs WHERE batch_id = ? AND withdrawn = 0 ORDER BY position",
                (batch_id,),
            )
            if not inputs:
                raise TaskError("message_batch_empty", "no active inputs remain")
            events = [event_from_document(json.loads(item["event_json"]), context) for item in inputs]
            captured_knowledge = self._knowledge_contributions(
                context, events, row["role_instruction"], self.tasks.now(), batch_id
            )
            contributions = tuple(contributions) + tuple(captured_knowledge)
        current_text = ""
        if context.scope.kind is ScopeKind.PRIVATE and self.cross_window_context is not None and inputs:
            parts = []
            for item in inputs:
                try:
                    data = json.loads(item["event_json"])
                    record = self._read_source(source_from_document(data["source"]), context,
                                               data["capability"], self.tasks.now())
                except Exception:
                    continue
                if record.payload:
                    parts.append(record.payload)
            current_text = "\n".join(parts)
        if len(contributions) > 32:
            raise ValueError("context contributions must be bounded")
        material_refs = []
        for contribution in contributions:
            if contribution.state is ContributionState.READY and contribution.source is None:
                raise ValueError("durable ready material requires a governed source")
            material_refs.append({**asdict(contribution), "payload": None})
        # Expression style now covers group turns as well, so its rotation bounds
        # are read for this exact scope before the sealing transaction opens.
        style_policy = None
        if self.expression_style_context is not None and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            style_policy = read_expression_settings(self.runtime, context)
        group_context_max_age_hours = None
        if context.scope.kind is ScopeKind.GROUP:
            from .participation import group_participation_settings
            group_context_max_age_hours = group_participation_settings(self.runtime, context)["group_context_max_age_hours"]
        with self.runtime.transaction() as connection:
            if row["state"] != "open":
                if row["generation_task_id"] is not None and row["state"] in ("sealed", "ready"):
                    task = require(self.tasks.read(context, row["generation_task_id"]))
                    if (task.spec.deadline_at != deadline_at or contributions and
                            json.loads(row["contributions_json"])["materials"] != json.loads(encode(material_refs))):
                        raise TaskError("message_seal_conflict", "original sealed inputs cannot be changed")
                    return task
                raise TaskError("message_batch_closed", "original batch cannot generate another reply")
            task_id = "generate-" + batch_id
            saved = {"inputs": [item["event_key"] for item in inputs], "materials": material_refs}
            saved["person_card"] = self.acquaintance.capture(context, self.tasks.now())
            if (self.cross_window_context is not None
                    and context.scope.kind in (ScopeKind.PRIVATE, ScopeKind.GROUP)):
                try:
                    saved["cross_window"] = self.cross_window_context.capture(
                        context, self.tasks.now(), current_text=current_text)
                except Exception as error:
                    logger.warning("V2 cross-window snapshot failed: %s", type(error).__name__)
            profile_snapshot = self.role_profile_context.capture(context, self.tasks.now())
            if profile_snapshot is not None:
                saved["role_profile"] = profile_snapshot
            relationship_profile_snapshot = self.relationship_profile_context.capture(context, self.tasks.now())
            if relationship_profile_snapshot is not None:
                saved["relationship_profile"] = relationship_profile_snapshot
            if self.reply_settings is not None:
                saved["reply_settings"] = self.reply_settings.capture(context, self.tasks.now())
            if context.scope.kind is ScopeKind.PRIVATE and self.perception_settings is not None:
                perception = self.perception_settings.capture(context, self.tasks.now())
                annual_calendars = ""
                if self.role_state_context is not None:
                    state_settings, _ = self.role_state_context.service.settings(context)
                    annual_calendars = state_settings["planning_annual_calendars"]
                saved["perception"] = {**perception, "annual_calendars": annual_calendars}
                contact_snapshot = self.perception_settings.configuration.read(
                    context.actor, target=contact_configuration_target_for(context),
                    group_id=CONTACT_CONFIGURATION_GROUP, observed_at=self.tasks.now())
                if contact_snapshot.is_success:
                    contact_values = contact_snapshot.unwrap().values
                    saved["important_dates"] = {
                        "settings": {
                            "important_dates": contact_values[CONTACT_CONFIGURATION_GROUP + ".important_dates"],
                            "lookahead_days": contact_values[CONTACT_CONFIGURATION_GROUP + ".important_date_lookahead_days"],
                            "timezone": contact_values[CONTACT_CONFIGURATION_GROUP + ".quiet_timezone"],
                        },
                        "configuration_token": contact_snapshot.unwrap().resolution_token,
                    }
            if context.scope.kind is ScopeKind.GROUP:
                saved["group_context"] = self.group_context.capture(
                    context, batch_id, self.tasks.now(), max_age_hours=group_context_max_age_hours
                )
                saved["group_memory"] = self.group_memory.context.capture(context, batch_id)
                saved["group_network"] = self.group_network.capture(context, events, at=self.tasks.now())
            private = context.scope.kind is ScopeKind.PRIVATE
            if private and self.relationship_context is not None:
                saved["relationship_context"] = self.relationship_context.capture(context, self.tasks.now())
            if private and self.memory_context is not None:
                references = tuple(source_from_document(json.loads(item["event_json"])["source"]) for item in inputs)
                saved["memory_context"] = self.memory_context.capture(context, batch_id, references, deadline_at)
            if private and self.life_context is not None:
                saved["life_context"] = self.life_context.capture(context, self.tasks.now())
            if private and self.role_growth_context is not None:
                saved["role_growth"] = self.role_growth_context.capture(context, self.tasks.now())
            if private and self.role_self_review_context is not None:
                saved["role_self_review"] = self.role_self_review_context.capture(context, self.tasks.now())
            if self.expression_style_context is not None and style_policy is not None:
                saved["expression_style"] = self.expression_style_context.capture(
                    context, self.tasks.now(), policy=style_policy)
            if private and self.role_state_context is not None:
                saved["role_state"] = self.role_state_context.capture(context, self.tasks.now())
            if private and self.open_items_context is not None:
                saved["open_items"] = self.open_items_context.capture(context, self.tasks.now())
            if private and self.background_history is not None:
                saved["background_history"] = self.background_history.capture(context, self.tasks.now())
            flow_row = self.runtime.read_one(
                "SELECT * FROM conversation_flows WHERE batch_id = ?", (batch_id,))
            if flow_row is not None and flow_row["reason"] == "busy_delay":
                saved["busy_delay"] = True
            not_before = self.tasks.now()
            if deadline_at <= not_before:
                raise TaskError("deadline_elapsed", "reply deadline elapsed while conversation context was being sealed")
            task = TaskSpec(task_id, context, REPLY_KIND, batch_id, batch_id, not_before, deadline_at)
            connection.execute("""UPDATE conversation_batches SET state = 'sealed', revision = revision + 1,
                generation_task_id = ?, contributions_json = ? WHERE batch_id = ?""",
                (task_id, encode(saved), batch_id))
            if (context.scope.kind is ScopeKind.GROUP and flow_row is not None
                    and flow_row["reason"] in ("debounce", "busy_delay") and inputs):
                try:
                    timing_values = json.loads(flow_row["policy_json"])
                    max_wait = float(timing_values["maximum_wait_seconds"])
                    started_at = datetime.fromisoformat(flow_row["merge_until"]) - timedelta(seconds=max_wait)
                    debounce_log.settled(
                        "group", target=context.scope.native_scope_id,
                        inputs=len(inputs), followups=max(0, len(inputs) - 1),
                        waited_ms=max(0.0, (not_before - started_at).total_seconds() * 1000),
                        timing_text=debounce_log.timing(
                            quiet_ms=float(timing_values["quiet_seconds"]) * 1000,
                            max_wait_ms=max_wait * 1000),
                    )
                except (KeyError, TypeError, ValueError):
                    pass
            return require(self.tasks.submit(task))

    @outcome
    def validate_generation(self, spec: TaskSpec, at: datetime):
        row = self.batch(spec.request_ref)
        if row["origin_key"] != spec.context.key or row["generation_task_id"] != spec.task_id or spec.kind != REPLY_KIND:
            raise TaskError("message_task_mismatch", "generation must use the original batch task")
        if row["state"] not in ("sealed", "ready"):
            raise TaskError("message_batch_closed", "input batch was cancelled or withdrawn")
        self._request(row, spec.context, at, include_tools=False)
        return True

    def _record_open_items_exposure(self, row, context, request):
        if self.open_items_context is None or context.scope.kind is not ScopeKind.PRIVATE:
            return request
        saved = json.loads(row["contributions_json"])
        snapshot = saved.get("open_items") if isinstance(saved, dict) else None
        if snapshot is None:
            return request
        candidates = tuple(material for material in request.context.materials
                           if material.owner == "agenda.memo" and material.purpose == "participant_open_item")
        if not candidates:
            return request
        try:
            exposed = self.open_items_context.record_exposure(snapshot, context, self.tasks.now(), candidates)
        except Exception as error:
            logger.warning("V2 open-item exposure could not be persisted; omitting optional context error_type=%s",
                           type(error).__name__)
            exposed = frozenset()

        omitted_items = False
        for material in candidates:
            try:
                payload = json.loads(material.payload)
                memo_id = payload.get("memo_id") if isinstance(payload, dict) else None
            except (TypeError, ValueError):
                memo_id = None
            if type(memo_id) is not str or memo_id not in exposed:
                omitted_items = True
                break
        materials = []
        for material in request.context.materials:
            if material.owner == "agenda.memo" and material.purpose == "participant_open_item":
                try:
                    payload = json.loads(material.payload)
                    memo_id = payload.get("memo_id") if isinstance(payload, dict) else None
                except (TypeError, ValueError):
                    memo_id = None
                if type(memo_id) is not str or memo_id not in exposed:
                    continue
            if material.owner == "agenda.memo" and material.purpose == "open_items":
                if not exposed:
                    continue
                try:
                    details = json.loads(material.payload)
                except (TypeError, ValueError):
                    details = {}
                if not isinstance(details, dict):
                    details = {}
                if omitted_items:
                    details["selection_partial"] = True
                    material = replace(material, payload=json.dumps(details, separators=(",", ":")))
            materials.append(material)
        if not omitted_items:
            return request
        instruction = request.instruction
        if not exposed:
            instruction = instruction.replace("\n" + OPEN_ITEMS_INSTRUCTION, "")
        return replace(request, instruction=instruction,
                       context=replace(request.context, materials=tuple(materials)))

    def request(self, run, *, include_tools=True):
        run.validate()
        at = self.tasks.now()
        if include_tools:
            self._prepare_daily_outfit(run, at)
        with self.runtime.read_snapshot():
            run.validate()
            row = self.batch(run.task.spec.request_ref)
            require(self.validate_generation(run.task.spec, self.tasks.now()))
            request = self._request(row, run.task.spec.context, at, include_tools=include_tools)
        return self._record_open_items_exposure(row, run.task.spec.context, request)

    @staticmethod
    def optional_guidance(material):
        return (material.owner, material.purpose) in {
            ("memory.person", "current_person_card"),
            ("role.expression", "expression_style_context"),
            ("role.expression", "expression_style_rule"),
            ("role.self_review", "self_review_context"),
            ("role.self_review", "self_review_guidance"),
        }

    def validate_optional_refresh(self, context, batch_id, original, request):
        """Only the owners may remove lapsed optional guidance during a turn."""
        old = tuple(m for m in original.context.materials if self.optional_guidance(m))
        new = tuple(m for m in request.context.materials if self.optional_guidance(m))
        if old == new:
            return False
        actual = self._request(self.owned(context, batch_id), context, self.tasks.now(), include_tools=False)
        expected = tuple(m for m in actual.context.materials if self.optional_guidance(m))
        style_purposes = {"expression_style_context", "expression_style_rule"}
        other = lambda items: tuple(m for m in items if m.purpose not in style_purposes)
        if other(new) != other(expected):
            return False
        previous_other = iter(other(old))
        if any(not any(material == previous for previous in previous_other) for material in other(new)):
            return False

        def style_rules(items):
            return tuple(m for m in items if m.purpose == "expression_style_rule")

        def ordered_subset(items, candidates):
            remaining = iter(candidates)
            return all(any(item == candidate for candidate in remaining) for item in items)

        selected = style_rules(new)
        if not ordered_subset(selected, style_rules(old)) or not ordered_subset(selected, style_rules(expected)):
            return False
        summaries = tuple(m for m in new if m.purpose == "expression_style_context")
        if not selected:
            return not summaries
        sources = tuple(m for m in expected if m.purpose == "expression_style_context")
        if len(summaries) != 1 or len(sources) != 1:
            return False
        try:
            current_info, source_info = json.loads(summaries[0].payload), json.loads(sources[0].payload)
            for name in ("context_at", "rules_available", "learning_mode"):
                if current_info.get(name) != source_info.get(name):
                    return False
            if (current_info.get("rules_injected") != len(selected)
                    or current_info.get("selection_partial") != (
                        len(selected) < current_info["rules_available"])):
                return False
        except (TypeError, ValueError, AttributeError, KeyError):
            return False
        return True

    def _prepare_daily_outfit(self, run, at):
        if (self.reference_materials is None or self.tools is None
                or run.task.spec.context.scope.kind is not ScopeKind.PRIVATE):
            return
        context = run.task.spec.context
        try:
            if not any(tool.action == "image.edit" for tool in
                       self.tools.catalog_for(context, run.task.spec.request_ref)):
                return
            row = self.batch(run.task.spec.request_ref)
            saved = json.loads(row["contributions_json"])
            perception = saved.get("perception") if isinstance(saved, dict) else None
            timezone_name = (validate_perception_snapshot(perception["settings"])["calendar_timezone"]
                             if perception is not None else self.timezone_name)
            from zoneinfo import ZoneInfo
            local_date = at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
            self.reference_materials.prepare_daily_outfit(context, local_date=local_date)
        except TaskError:
            return

    def journal_inputs(self, context: TaskContext, *, limit=50, after_id=None) -> JournalConversationInputPage:
        """Read one authorized page of active, accepted inbound messages for private writing."""
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("journal input page size must be between 1 and 64")
        if after_id is not None and (not isinstance(after_id, str) or not after_id.strip()):
            raise ValueError("journal input cursor must be a non-blank event key")
        with self.runtime.read_snapshot():
            self.authorize(context)
            cursor_clause = ""
            parameters = [context.key]
            if after_id is not None:
                cursor = self.runtime.read_one(
                    """SELECT i.event_json FROM conversation_inputs i
                       JOIN conversation_batches b ON b.batch_id = i.batch_id
                       WHERE b.origin_key = ? AND i.event_key = ?""",
                    (context.key, after_id))
                if cursor is None:
                    raise TaskError("message_cursor_invalid", "writing cursor is not part of this original conversation")
                cursor_event = event_from_document(json.loads(cursor["event_json"]), context)
                cursor_time = stamp(cursor_event.received_at)
                cursor_clause = """AND (json_extract(i.event_json, '$.received_at') > ?
                    OR (json_extract(i.event_json, '$.received_at') = ? AND i.event_key > ?)) """
                parameters.extend((cursor_time, cursor_time, after_id))
            parameters.append(limit)
            rows = self.runtime.read_all(
                """SELECT i.event_key, i.event_json FROM conversation_inputs i
                   JOIN conversation_batches b ON b.batch_id = i.batch_id
                   WHERE b.origin_key = ? AND i.withdrawn = 0 """ + cursor_clause +
                " ORDER BY json_extract(i.event_json, '$.received_at'), i.event_key LIMIT ?", tuple(parameters))
            items = []
            for row in rows:
                event = event_from_document(json.loads(row["event_json"]), context)
                if (event.scoped_key != row["event_key"] or event.scope != context.scope
                        or event.principal != context.actor.principal):
                    raise TaskError("message_context_mismatch", "stored writing input must retain its original route and sender")
                source = self._read_source(event.source, context, event.capability, self.tasks.now())
                if source.content_hash != event.input_hash:
                    raise TaskError("message_input_changed", "writing input hash no longer matches its governed source")
                items.append(JournalConversationInput(event, source))
            next_cursor = rows[-1]["event_key"] if len(rows) == limit else None
            return JournalConversationInputPage(tuple(items), next_cursor)

    @outcome
    def cancel(self, context: TaskContext, batch_id: str):
        with self.runtime.transaction() as connection:
            row = self.owned(context, batch_id)
            if row["state"] not in ("cancelled", "invalidated"):
                connection.execute("UPDATE conversation_batches SET state = 'cancelled', revision = revision + 1 WHERE batch_id = ?", (batch_id,))
                connection.execute("UPDATE conversation_parts SET state = 'cancelled' WHERE batch_id = ? AND state = 'pending'", (batch_id,))
            return batch_id

    @outcome
    def withdraw(self, context: TaskContext, event_key: str):
        with self.runtime.transaction() as connection:
            self.authorize(context)
            item = connection.execute("SELECT * FROM conversation_inputs WHERE event_key = ?", (event_key,)).fetchone()
            if item is None:
                raise TaskError("message_event_unavailable", "no original input exists in this route")
            row = self.batch(item["batch_id"])
            original = TaskContext.restore(json.loads(row["context_json"]))
            if (context.scope != original.scope or context.actor.principal != original.actor.principal or
                    (context.actor.subject, context.actor.binding_id, context.actor.binding_revision) !=
                    (original.actor.subject, original.actor.binding_id, original.actor.binding_revision)):
                raise TaskError("message_event_unavailable", "withdrawal belongs to another authenticated author")
            if item["withdrawn"]:
                return row["batch_id"]
            event_data = json.loads(item["event_json"])
            source = source_from_document(event_data["source"])
            source_row = connection.execute(
                """SELECT governance_revision, state FROM sources
                   WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""",
                (source.source_id, source.version.lineage_id, source.version.revision)).fetchone()
            if source_row is not None and source_row["state"] == "active":
                require(self.sources.revoke(source, expected_governance_revision=source_row["governance_revision"],
                                            observed_at=self.tasks.now()))
            connection.execute("UPDATE conversation_inputs SET withdrawn = 1 WHERE event_key = ?", (event_key,))
            state = "open" if row["state"] == "open" else "invalidated"
            connection.execute("UPDATE conversation_batches SET state = ?, revision = revision + 1 WHERE batch_id = ?", (state, row["batch_id"]))
            connection.execute("UPDATE conversation_parts SET state = 'cancelled' WHERE batch_id = ? AND state = 'pending'", (row["batch_id"],))
            return row["batch_id"]

    @outcome
    def observe(self, context: TaskContext, batch_id: str):
        with self.runtime.read_snapshot():
            row = self.owned(context, batch_id)
            inputs = self.runtime.read_all("SELECT event_key, withdrawn FROM conversation_inputs WHERE batch_id = ? ORDER BY position", (batch_id,))
            parts = self.runtime.read_all("""SELECT part_id, sequence, state, task_id, dispatched_at, delivered_at
                FROM conversation_parts WHERE batch_id = ? ORDER BY sequence""", (batch_id,))
            tool_calls = self.runtime.read_all("""SELECT sequence, action, state, effect, operation_id IS NOT NULL AS has_owner_receipt
                FROM conversation_tool_calls WHERE batch_id = ? ORDER BY sequence""", (batch_id,))
            registered_actions = self.tools.actions if self.tools is not None else frozenset()
            return {"batch_id": batch_id, "state": row["state"], "revision": row["revision"],
                    "generation_task_id": row["generation_task_id"], "input_count": len(inputs),
                    "withdrawn_count": sum(item["withdrawn"] for item in inputs),
                    "confirmed_parts": sum(part["state"] == "delivered" for part in parts),
                    "unresolved_parts": sum(part["state"] in ("dispatched", "accepted", "unknown") for part in parts),
                    "receipt_basis": "local_test_adapter", "recipient_read": "unknown",
                    "tool_calls": self.tools.observe(batch_id) if self.tools else [{**dict(call),
                                    "action": call["action"] if call["action"] in registered_actions else "unregistered",
                                    "state": "unknown" if call["state"] == "started" else call["state"],
                                    "has_owner_receipt": bool(call["has_owner_receipt"])} for call in tool_calls],
                    "parts": [dict(part) for part in parts], "sender": "not_connected" if not any(
                        part["task_id"] for part in parts) else "local_test_adapter"}

    @outcome
    def observe_task(self, context: TaskContext, task_id: str):
        with self.runtime.read_snapshot():
            task = require(self.tasks.read(context, task_id))
            if task.spec.kind == REPLY_KIND:
                batch_id = task.spec.request_ref
            elif task.spec.kind == DELIVERY_KIND:
                row = self.runtime.read_one("SELECT batch_id FROM conversation_parts WHERE part_id = ? AND task_id = ?",
                                            (task.spec.request_ref, task_id))
                if row is None:
                    raise TaskError("message_part_unavailable", "original delivery part is unavailable")
                batch_id = row["batch_id"]
            else:
                return {"state": "not_applicable", "sender": "not_connected", "unresolved_parts": 0}
            return require(self.observe(context, batch_id))

    @outcome
    def history(self, context: TaskContext, *, after: int = 0, limit: int = 32):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history requires a bounded cursor page")
        with self.runtime.read_snapshot():
            self.authorize(context)
            rows = self.runtime.read_all("""SELECT h.*, p.sequence, p.text, p.platform_ref, b.presentation_action
                FROM conversation_sent_history h
                JOIN conversation_parts p ON p.part_id = h.part_id
                JOIN conversation_batches b ON b.batch_id = h.batch_id
                WHERE h.origin_key = ? AND h.history_sequence > ? ORDER BY h.history_sequence LIMIT ?""",
                (context.key, after, limit))
            available = {}
            sent_images = {}
            items = []
            for row in rows:
                if row["batch_id"] not in available:
                    try:
                        batch = self.batch(row["batch_id"])
                        request = self._request(batch, context, self.tasks.now(), historical=True)
                        originals, _, image_part_id = self.reply_images.read_binding(
                            batch, context, self.tasks.now(), request=request)
                        sent_images[row["batch_id"]] = (image_part_id, self.reply_images.describe(originals))
                        available[row["batch_id"]] = True
                    except TaskError:
                        available[row["batch_id"]] = False
                readable = available[row["batch_id"]]
                try:
                    self.recalls.check_reply(context.scope, row["platform_ref"])
                except TaskError:
                    readable = False
                items.append({"cursor": row["history_sequence"], "part_id": row["part_id"],
                              "batch_id": row["batch_id"], "sequence": row["sequence"],
                              "delivered_at": row["delivered_at"], "body_available": readable,
                              "text": row["text"] if readable else None})
                if row["presentation_action"] != "text":
                    items[-1]["action"] = row["presentation_action"]
                if readable:
                    image_part_id, images = sent_images[row["batch_id"]]
                    if images and row["part_id"] == image_part_id:
                        items[-1]["images"] = images
            return {"items": items, "next_cursor": items[-1]["cursor"] if items else after}
