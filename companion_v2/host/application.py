"""One explicit composition root for governed conversations and private domains.

The host supplies authenticated input, model/tool/sender ports and V2 storage.
All ports must be trusted cooperative async implementations. No AstrBot import,
production entry point or data discovery lives here. Explicit domain schedules
share the host wake loop and lifecycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
import json
import traceback

from companion_v2.agenda.background import AgendaBackground, AgendaDispatchPolicy, OWNER as AGENDA_OWNER
from companion_v2.agenda.service import AgendaService
from companion_v2.agenda.calendar import CalendarService
from companion_v2.agenda.planning import CalendarPlanning
from companion_v2.role_work.state.configuration import (
    StateConfigurationOwner, configuration_fields as state_configuration_fields, migrate_presence_to_role,
)
from companion_v2.role_work.state.service import RoleStateService
from companion_v2.role_work.state.maintenance import StateMaintenance
from companion_v2.capabilities.ownership import DEFAULT_MEMORY_OWNER
from companion_v2.capabilities.memory_push import DEFAULT_MEMORY_PUSH
from companion_v2.capabilities.read_providers import ReadProviderRegistry
from companion_v2.conversation.growth_context import ConversationRoleGrowthContext
from companion_v2.conversation.self_review_context import ConversationSelfReviewContext, SELF_REVIEW_INSTRUCTION
from companion_v2.conversation.style_context import ConversationExpressionStyleContext, EXPRESSION_STYLE_INSTRUCTION
from companion_v2.conversation.open_items_context import OpenItemsReader
from companion_v2.configuration.models import ConfigurationError
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation.delivery import LocalConversationDelivery
from companion_v2.conversation.pokes import PokeReplyModel
from companion_v2.conversation.flow import ConversationFlow, ConversationPolicy
from companion_v2.conversation.generation import LocalReplyGenerator, single_part
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.reply_settings import ReplyConfigurationOwner, ReplySettings, configuration_fields as reply_configuration_fields
from companion_v2.conversation import busy_gate
from companion_v2.conversation.busy_gate import (BusyGateConfigurationOwner,
    configuration_fields as busy_gate_configuration_fields)
from companion_v2.conversation.participation import (GroupParticipationConfigurationOwner,
    configuration_fields as group_participation_configuration_fields)
from companion_v2.conversation.perception_settings import (PerceptionConfigurationOwner, PerceptionSettings,
    configuration_fields as perception_configuration_fields)
from companion_v2.conversation.media.quota import (ImageQuotaConfigurationOwner,
    configuration_fields as image_quota_configuration_fields)
from companion_v2.conversation.media.output_settings import (ImageOutputConfigurationOwner,
    configuration_fields as image_output_configuration_fields)
from companion_v2.conversation.media.review import (ImageReviewConfigurationOwner, GroupImageReview,
    configuration_fields as image_review_configuration_fields)
from companion_v2.conversation.media.review_service import GroupImageReviewService, image_review_profile
from companion_v2.conversation.media.image_narration import PrivateImageNarrationService
from companion_v2.conversation.media.video.narration import NativeVideoNarrationService
from companion_v2.conversation.media.context_caption import ContextImageCaptionService
from companion_v2.conversation.content.authoring_workflow import ContentAuthoringWorkflow
from companion_v2.conversation.media.fallback import ImageReviewFallback, OWNER as FALLBACK_OWNER
from companion_v2.conversation.messages import ConversationMessages, DELIVERY_KIND, REPLY_ACTION, REPLY_KIND
from companion_v2.conversation.tool_loop import LocalRoleTools
from companion_v2.diagnostics.service import DiagnosticService
from companion_v2.delivery.service import DeliveryOwner, OutboundDelivery
from companion_v2.delivery.history import BackgroundHistory
from companion_v2.execution.models import Lane, SideEffect, SupervisorLimits, TaskDefinition, TaskError, TaskRegistration, require
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.waiting import BackgroundTaskQueue
from companion_v2.execution.service import TaskSupervisor, outcome
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.host.background import BackgroundCoordinator
from companion_v2.model_gateway.budget import BudgetLedger, configuration_fields as budget_configuration_fields
from companion_v2.model_gateway.routing import CONSUMER, ModelRouter
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.role_work.ledger import PersistentRoleLifeLedger
from companion_v2.role_work.growth import PersistentRoleGrowthLedger, RoleGrowthContextReader
from companion_v2.role_work.growth.configuration import (GrowthConfigurationOwner,
    SkillConfigurationOwner, configuration_fields as growth_configuration_fields,
    skill_configuration_fields)
from companion_v2.role_profile.configuration import (RoleProfileConfigurationOwner,
    configuration_fields as role_profile_configuration_fields)
from companion_v2.relationships.profile import (RelationshipProfileConfigurationOwner,
    configuration_fields as relationship_profile_configuration_fields)
from companion_v2.role_work.growth.progress import GrowthProgressService
from companion_v2.role_work.reading import RoleReadingService
from companion_v2.role_work.reader import RoleReader
from companion_v2.role_work.life.coordinator import RoleLifeCoordinator
from companion_v2.role_work.life.context import RoleLifeContextReader
from companion_v2.role_work.life.configuration import LifeConfigurationOwner
from companion_v2.role_work.timeline import RoleTimeline
from companion_v2.role_work.life.configuration import configuration_fields as life_configuration_fields
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.role_work.journal import (RoleActivityJournalMaterialProducer, RoleConversationJournalMaterialProducer, RoleJournalService,
                                            RoleTextArtifactJournalMaterialProducer)
from companion_v2.role_work.journal.writing import RoleJournalWriter
from companion_v2.role_work.journal.collection import JournalMaterialCollector
from companion_v2.role_work.journal.reading_materials import RoleReadingJournalMaterialProducer
from companion_v2.screen.repositories import ScreenRepository
from companion_v2.screen.journal_materials import ScreenJournalMaterialProducer
from companion_v2.screen.configuration import (ScreenConfigurationOwner, ScreenSettings,
    configuration_fields as screen_configuration_fields)
from companion_v2.screen.interpretation import ScreenInterpretationService
from companion_v2.screen.peek import ScreenPeekService
from companion_v2.conversation.relay_settings import (RelayConfigurationOwner, RelaySettings,
    configuration_fields as relay_configuration_fields)
from companion_v2.conversation.recall_settings import (RecallConfigurationOwner, RecallSettings,
    configuration_fields as recall_configuration_fields)
from companion_v2.conversation.web_understanding import (WebUnderstandingConfigurationOwner,
    WebUnderstandingSettings, configuration_fields as web_configuration_fields)
from companion_v2.library.configuration import (LibraryConfigurationOwner, LibrarySettings,
    configuration_fields as library_configuration_fields)
from companion_v2.library.provider import (CATALOG_CAPABILITY as LIBRARY_CATALOG_CAPABILITY,
    READ_CAPABILITY as LIBRARY_READ_CAPABILITY, make_catalog_provider as make_library_catalog_provider,
    make_read_provider as make_library_read_provider)
from companion_v2.references.configuration import (ReferenceConfigurationOwner, ReferenceSettings,
    configuration_fields as reference_configuration_fields)
from companion_v2.references.service import ReferenceAssetService
from companion_v2.references.wardrobe import (WardrobeConfigurationOwner, WardrobeSettings,
    configuration_fields as wardrobe_configuration_fields)
from companion_v2.qzone.configuration import (QzoneConfigurationOwner, QzoneSettings,
    configuration_fields as qzone_configuration_fields)
from companion_v2.qzone.service import QzoneService
from companion_v2.relationships.private_access import (PrivateAccessSettings,
    configuration_fields as private_access_configuration_fields)
from companion_v2.relationships.private_notice import PrivateAccessGate, PrivateRefusalNotice
from companion_v2.library.service import ReadingArchiveService
from companion_v2.role_work.journal.triggers import JournalTriggers
from companion_v2.role_work.journal.scheduling import JournalScheduleDriver
from companion_v2.memory.activity_receipts import RoleReceiptReader
from companion_v2.expression.style_evidence import ExpressionSampleCatalog
from companion_v2.expression.style_service import ExpressionStyleService
from companion_v2.expression.review import ExpressionDraftReview
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.configuration import (ExpressionConfigurationOwner,
    GroupExpressionConfigurationOwner, PrivateExpressionConfigurationOwner,
    configuration_fields as expression_configuration_fields,
    group_configuration_fields as expression_group_configuration_fields,
    private_configuration_fields as expression_private_configuration_fields)
from companion_v2.expression.reaction_configuration import (ReactionConfigurationOwner,
    configuration_fields as reaction_configuration_fields)
from companion_v2.knowledge.configuration import (KnowledgeConfigurationOwner,
    configuration_fields as knowledge_configuration_fields)
from companion_v2.conversation.content.authoring_service import (
    configuration_fields as content_authoring_configuration_fields)
from companion_v2.conversation.content.cover_assets import (
    configuration_fields as content_cover_configuration_fields)
from companion_v2.knowledge.context import knowledge_context
from companion_v2.role_work.life import rest_gate
from companion_v2.role_work.journal.self_review_evidence import SelfReviewEvidenceCatalog
from companion_v2.role_work.journal.self_review_service import SelfReviewService
from companion_v2.role_work.journal.self_review_management import SelfReviewManagement
from companion_v2.relationships.service import RelationshipMemoryService
from companion_v2.memory.service import MemoryService
from companion_v2.memory.provider import CAPABILITY as MEMORY_QUERY_CAPABILITY, make_provider as make_memory_query_provider
from companion_v2.memory.provider import (OPERATION_CAPABILITY as MEMORY_OPERATION_CAPABILITY,
    make_operation_provider as make_memory_operation_provider, make_commands as make_memory_commands,
    make_subscriptions as make_memory_subscriptions)
from companion_v2.memory.stream_consumer import MemoryReferenceConsumer
from companion_v2.memory.models import MemoryPolicy
from companion_v2.memory.reflection import MemoryReflection
from companion_v2.memory.scheduling import MemorySchedule
from companion_v2.memory.configuration import extend_registry
from companion_v2.memory.person.configuration import (AcquaintanceSettings,
    configuration_fields as person_configuration_fields,
    learning_configuration_fields as person_learning_configuration_fields)
from companion_v2.role_work.awareness import RoleAwareness
from companion_v2.conversation.awareness_context import ConversationAwarenessContext
from companion_v2.host.role_attention import RoleAttention
from companion_v2.proactive.configuration import ContactConfigurationOwner, GROUP as CONTACT_GROUP, with_contact_fields
from companion_v2.proactive.service import ProactiveContacts
from companion_v2.proactive.scheduling import ContactSchedule


logger = getLogger(__name__)


@dataclass(frozen=True)
class StartupReport:
    task_generation: int
    model_generation: int
    recovered_tasks: int
    unresolved_deliveries: int
    released_worker_slots: int = 0


@dataclass(frozen=True)
class ShutdownReport:
    state: str
    pending_handles: tuple[str, ...]
    storage_closed: bool


class CompanionApplication:
    WAL_CHECKPOINT_INTERVAL_SECONDS = 30.0
    # Expired model reservations only become eligible after twice their task
    # timeout, so a one-minute maintenance pass never competes with live work.
    BUDGET_RECLAIM_INTERVAL_SECONDS = 60.0

    def __init__(self, runtime, *, registry, profiles, providers, budget, sender, external=None,
                 policy=ConversationPolicy(), limits=SupervisorLimits(), split=single_part,
                 now=None, owns_runtime=False, agenda_policy=AgendaDispatchPolicy(), memory_policy=MemoryPolicy(),
                 runtime_identity=None, presence_port=None, segmentation=SegmentationPolicy(), voice_policy=None,
                 weather_port=None, weather_alert_port=None, balance_port=None, news_port=None,
                 solar_term_port=None, budget_soft=None, capability_owners=(),
                 qzone_transport=None, default_provider_resolver=None,
                 knowledge=None, memory_owner=DEFAULT_MEMORY_OWNER, memory_push=DEFAULT_MEMORY_PUSH, wake_port=None,
                 search_port_factory=None):
        if type(owns_runtime) is not bool or not any(p.kind == REPLY_KIND and p.lane is Lane.FOREGROUND for p in profiles):
            raise ValueError("assembly requires a foreground reply profile and explicit runtime ownership")
        if external is not None and (external.runtime is not runtime or external.messages is not None):
            raise ValueError("external executor must be unattached and use this V2 runtime")
        external = external if external is not None and external.registrations else None
        if external is not None and limits.foreground_slots < 2:
            raise ValueError("external tools need capacity for both parent and child")
        self.runtime, self.owns_runtime = runtime, owns_runtime
        self.now = now or (lambda: datetime.now(timezone.utc))
        # 关键词材料（如服务余额）只有登记项自己需要时才会去取；端口留着由它按需调用。
        self.balance_port = balance_port
        self.keyword_materials = None
        self.role_attention = RoleAttention()
        self.external = external
        self.semantics = None
        # Optional Laya resolver is attached by the host after assembly.  It is
        # resolved per conversation context so one shared application can serve
        # multiple targets and roles without sharing credentials.
        self.laya_decision_resolver = None
        self.image_review = None
        # Reserve a child slot for each concurrently running parent when tools are installed.
        self.worker_limit = limits.foreground_slots // 2 if external is not None else limits.foreground_slots
        registry = extend_registry(registry, memory_policy)
        reply_provider = next(p.default_provider for p in profiles if p.kind == REPLY_KIND)
        streaming = next(p.default_streaming for p in providers if p.binding.provider == reply_provider)
        extra = tuple(f for f in (*life_configuration_fields(), *growth_configuration_fields(),
                                 *skill_configuration_fields(), *role_profile_configuration_fields(),
                                 *relationship_profile_configuration_fields(),
                                 *state_configuration_fields(),
            *reply_configuration_fields(segmentation, streaming, policy.part_gap_seconds, voice_policy),
            *perception_configuration_fields(), *person_configuration_fields(),
            *person_learning_configuration_fields(),
            *image_quota_configuration_fields(), *image_review_configuration_fields(),
            *image_output_configuration_fields(),
            *expression_configuration_fields(), *expression_private_configuration_fields(),
            *expression_group_configuration_fields(), *reaction_configuration_fields(),
            *busy_gate_configuration_fields(), *group_participation_configuration_fields(),
            *knowledge_configuration_fields(), *budget_configuration_fields(),
            *screen_configuration_fields(), *recall_configuration_fields(),
            *web_configuration_fields(),
            *library_configuration_fields(),
            *reference_configuration_fields(),
            *wardrobe_configuration_fields(),
            *qzone_configuration_fields(),
            *private_access_configuration_fields(),
            *relay_configuration_fields(),
            *(field for owner in capability_owners for field in owner.fields),
            *content_authoring_configuration_fields(), *content_cover_configuration_fields()) if f.field_id not in registry.fields)
        if extra:
            registry = FieldRegistry(version=registry.version, fields=tuple(registry.fields.values()) + extra)
        reference_configuration_owner = ReferenceConfigurationOwner(runtime)
        self.configuration = ConfigurationService(runtime, with_contact_fields(registry),
            owners=(ContactConfigurationOwner(runtime), GrowthConfigurationOwner(runtime),
                SkillConfigurationOwner(runtime), RoleProfileConfigurationOwner(runtime),
                RelationshipProfileConfigurationOwner(runtime),
                StateConfigurationOwner(runtime),
                    LifeConfigurationOwner(runtime), ReplyConfigurationOwner(runtime, voice_policy),
                    PerceptionConfigurationOwner(runtime), ImageQuotaConfigurationOwner(runtime),
                    ImageOutputConfigurationOwner(runtime),
                    ImageReviewConfigurationOwner(runtime), ExpressionConfigurationOwner(runtime),
                    PrivateExpressionConfigurationOwner(runtime), GroupExpressionConfigurationOwner(runtime),
                    ReactionConfigurationOwner(runtime), BusyGateConfigurationOwner(runtime),
                    GroupParticipationConfigurationOwner(runtime), KnowledgeConfigurationOwner(runtime),
                    ScreenConfigurationOwner(runtime), RecallConfigurationOwner(runtime),
                    WebUnderstandingConfigurationOwner(runtime),
                    RelayConfigurationOwner(runtime),
                    LibraryConfigurationOwner(runtime),
                    reference_configuration_owner,
                    WardrobeConfigurationOwner(runtime),
                    QzoneConfigurationOwner(runtime),
                    *capability_owners))
        try:
            migrate_presence_to_role(runtime)
        except Exception as error:
            logger.warning("[CompanionV2] QQ presence scope migration failed error_type=%s", type(error).__name__)
        self.reply_settings = ReplySettings(self.configuration)
        self.perception_settings = PerceptionSettings(self.configuration)
        self.screen_settings = ScreenSettings(self.configuration)
        self.recall_settings = RecallSettings(self.configuration)
        self.relay_settings = RelaySettings(self.configuration)
        self.library_settings = LibrarySettings(self.configuration)
        self.reference_settings = ReferenceSettings(reference_configuration_owner)
        self.wardrobe_settings = WardrobeSettings(self.configuration)
        self.qzone_settings = QzoneSettings(self.configuration)
        # Without a host transport the extension is simply absent: no credentials,
        # no session and no partially usable Zone path.
        self.qzone = (QzoneService(runtime, self.qzone_settings, qzone_transport, now=self.now)
                      if qzone_transport is not None else None)
        self.proactive = ProactiveContacts(self.configuration, memory_push=memory_push)
        self.role_life = PersistentRoleLifeLedger(runtime, now=self.now)
        self.role_growth = PersistentRoleGrowthLedger(runtime, now=self.now)
        self.relationship_memories = RelationshipMemoryService(runtime, now=self.now)
        self.role_journal = RoleJournalService(runtime, now=self.now)
        self.journal_writer = RoleJournalWriter(self.role_journal, memory_push=memory_push)
        self.memory_reflection = MemoryReflection(runtime)
        self.role_reader = RoleReader(self.role_life)
        self.state_maintenance = StateMaintenance(runtime)
        self.calendar_planning = CalendarPlanning(runtime, weather_port=weather_port)
        self.growth_progress = GrowthProgressService(runtime)
        self.self_review = SelfReviewService(runtime)
        self.expression_style = ExpressionStyleService(runtime)
        self.expression_draft_review = ExpressionDraftReview(runtime)
        self.agenda = AgendaService(runtime)
        self.outbound = OutboundDelivery(runtime, sender, (
            DeliveryOwner(AGENDA_OWNER, "reminder.deliver", lambda request, at: self.agenda_background.delivery_text(request, at),
                lambda request, at: self.agenda_background.history_text(request, at)),
            DeliveryOwner(CONTACT_GROUP, CONTACT_GROUP, self.proactive.delivery_text, self.proactive.history_text),
            DeliveryOwner(FALLBACK_OWNER, "conversation.reply", lambda request, at: self.image_fallback.read(request, at)),
        ), concurrency=limits.background_slots)
        self.outbound.on_delivered = self._mark_contact_sent
        self.outbound.on_receipt = self._record_contact_delivery_receipt
        # The review executor only needs the runtime until its app is attached; the
        # conversation owner binds the real review instance once messages exist.
        self.image_review_service = GroupImageReviewService(runtime, notify=self.notify)
        self.context_image_caption = ContextImageCaptionService(runtime, notify=self.notify)
        self.image_narration = PrivateImageNarrationService(runtime, notify=self.notify)
        self.video_narration = NativeVideoNarrationService(runtime, notify=self.notify)
        self.screen_interpretation = ScreenInterpretationService(runtime, notify=self.notify)
        self.screen_peek = ScreenPeekService(runtime, notify=self.notify)
        self.authoring = ContentAuthoringWorkflow(runtime)
        self.poke_reply_model = PokeReplyModel(runtime, now=self.now)
        self.poke_context_tasks = set()
        registrations = (
            TaskRegistration(TaskDefinition(REPLY_KIND, "conversation", REPLY_ACTION, Lane.FOREGROUND,
                timeout_seconds=600, max_attempts=1, concurrency=self.worker_limit),
                lambda spec, at: self.messages.validate_generation(spec, at)),
            TaskRegistration(TaskDefinition(DELIVERY_KIND, "conversation.delivery", REPLY_ACTION, Lane.FOREGROUND,
                side_effect=SideEffect.MESSAGE_DELIVERY, binding=sender.binding, timeout_seconds=30,
                max_attempts=1, concurrency=self.worker_limit), lambda spec, at: self.delivery.validate(spec, at)),
        ) + self.poke_reply_model.registrations + self.journal_writer.registrations + self.memory_reflection.registrations + self.role_reader.registrations + self.state_maintenance.registrations + self.calendar_planning.registrations + self.growth_progress.registrations + self.self_review.registrations + self.expression_style.registrations + self.proactive.registrations + self.outbound.registrations + self.image_review_service.registrations + self.context_image_caption.registrations + self.image_narration.registrations + self.video_narration.registrations + self.screen_interpretation.registrations + self.screen_peek.registrations + self.authoring.registrations + (external.registrations if external is not None else ())
        registrations += self.expression_draft_review.registrations
        self.tasks = TaskSupervisor(runtime, registrations, limits=limits, now=self.now)
        # Reference material shares this runtime and the supervisor that owns it, so
        # it is constructed here rather than with the other settings readers above.
        self.references = ReferenceAssetService(runtime, self.tasks, settings=self.reference_settings,
                                                wardrobe=self.wardrobe_settings,
                                                require_running=self.require_running)
        self.role_state = RoleStateService(self.tasks, self.configuration, self.role_life)
        self.calendar = CalendarService(self.tasks, self.agenda, self.role_state)
        self.outbound.attach(self.tasks)
        # 私聊用户权限：默认值来自配置；权限本身仍只看 grant。
        # 拒绝的判定在核心，**发送在宿主层**（那里才有平台客户端，也才覆盖得了陌生号）。
        self.private_access_settings = PrivateAccessSettings(self.configuration)
        self.private_notice = PrivateRefusalNotice(runtime, self.tasks, settings=self.private_access_settings)
        self.private_access_gate = PrivateAccessGate(self.tasks, self.private_notice)
        self.agenda_background = AgendaBackground(self.agenda, self.tasks, self.outbound, policy=agenda_policy, calendar=self.calendar)
        self.memory = MemoryService(self.tasks, policy=memory_policy, configuration=self.configuration,
                                    owner=memory_owner)
        self.provider_registry = ReadProviderRegistry(self.tasks, require_running=self.require_running)
        # Standalone assembly must supply its own identity before exposing wire
        # scope. In-process memory consumers do not need a fabricated identity.
        self.memory_provider_registration = None
        self.memory_operation_registration = None
        self.memory_commands = None
        self.memory_subscriptions = None
        self.memory_reference_views = None
        self.library = ReadingArchiveService(runtime, self.tasks, self.configuration,
                                             require_running=self.require_running)
        self.library_catalog_registration = None
        self.library_read_registration = None
        if runtime_identity is not None:
            self.memory_provider_registration = self.provider_registry.register(
                "companion_v2.memory", MEMORY_QUERY_CAPABILITY,
                lambda binding: make_memory_query_provider(self.memory, binding, runtime_identity=runtime_identity),
            )
            self.memory_operation_registration = self.provider_registry.register(
                "companion_v2.memory", MEMORY_OPERATION_CAPABILITY,
                lambda binding: make_memory_operation_provider(self.memory, binding, runtime_identity=runtime_identity),
            )
            self.memory_commands = make_memory_commands(self.memory, runtime_identity=runtime_identity,
                                                        require_running=self.require_running)
            self.memory_subscriptions = make_memory_subscriptions(self.memory, runtime_identity=runtime_identity,
                                                                  require_running=self.require_running)
            self.memory_reference_views = MemoryReferenceConsumer(self.memory_subscriptions)
            self.library_catalog_registration = self.provider_registry.register(
                "companion_v2.library", LIBRARY_CATALOG_CAPABILITY,
                lambda binding: make_library_catalog_provider(self.library, binding,
                                                              runtime_identity=runtime_identity),
            )
            self.library_read_registration = self.provider_registry.register(
                "companion_v2.library", LIBRARY_READ_CAPABILITY,
                lambda binding: make_library_read_provider(self.library, binding,
                                                           runtime_identity=runtime_identity),
            )
        self.life_context = RoleLifeContextReader(self.role_life, self.role_journal, self.tasks)
        self.memory.attach_life(self.life_context, require_running=self.require_running)
        self.role_growth_context = ConversationRoleGrowthContext(
            RoleGrowthContextReader(self.role_growth, self.tasks, sources=self.role_journal.sources))
        self.role_timeline = RoleTimeline(self.life_context, self.role_growth_context.reader)
        self.open_items = OpenItemsReader(self.agenda, self.tasks)
        self.role_self_review_evidence = SelfReviewEvidenceCatalog(
            runtime, RoleReceiptReader(self.life_context))
        self.role_self_review_context = ConversationSelfReviewContext(self.role_self_review_evidence)
        self.expression_samples = ExpressionSampleCatalog(runtime)
        self.expression_style_context = ConversationExpressionStyleContext(self.expression_samples)
        self.messages = ConversationMessages(self.tasks, relationships=self.relationship_memories,
                                             memory=self.memory, life=self.life_context, open_items=self.open_items,
                                             role_growth=self.role_growth_context, role_state=self.role_state,
                                             role_self_review=self.role_self_review_context,
                                             expression_style=self.expression_style_context,
                                             calendar=self.calendar,
                                             reference_materials=self.references,
                                             knowledge=knowledge_context(runtime) if knowledge is None else knowledge,
                                             solar_term_port=solar_term_port)
        self.role_self_review_evidence.attach(self.messages)
        self.expression_samples.attach(self.messages)
        self.background_history = BackgroundHistory(self.outbound, messages=self.messages,
                                                    verify_platform_message=self.messages.recalls.check_reply)
        self.messages.background_history = self.background_history
        self.journal_material_producer = RoleActivityJournalMaterialProducer(self.role_life)
        self.journal_artifact_material_producer = RoleTextArtifactJournalMaterialProducer(self.role_life, tasks=self.tasks)
        self.journal_conversation_material_producer = RoleConversationJournalMaterialProducer(self.messages)
        self.screen_repository = ScreenRepository(runtime, authorizer=self.tasks.identity)
        self.journal_screen_material_producer = ScreenJournalMaterialProducer(
            self.tasks, self.screen_repository, settings=self.screen_settings)
        self.role_reading = RoleReadingService(self.role_life, self.tasks, require_running=self.require_running)
        self.journal_reading_material_producer = RoleReadingJournalMaterialProducer(self.role_life, self.tasks)
        self.journal_material_collector = JournalMaterialCollector(self.tasks, self.role_life,
            activities=self.journal_material_producer, artifacts=self.journal_artifact_material_producer,
            readings=self.journal_reading_material_producer, conversations=self.journal_conversation_material_producer,
            screens=self.journal_screen_material_producer,
            require_running=self.require_running)
        self.delivery = LocalConversationDelivery(self.messages, sender)
        self.messages.reply_images.enabled = sender.supports_images
        self.messages.reply_pokes = sender.supports_poke
        self.router = ModelRouter(self.configuration, profiles, providers,
                                  default_provider_resolver=default_provider_resolver)
        self.gateway = LocalModelGateway(self.router, BudgetLedger(runtime, budget, budget_soft), self.tasks)
        self.authoring.attach(self.gateway, require_running=self.require_running)
        self.background_waiting = BackgroundTaskQueue(self.tasks)
        self.poke_reply_model.attach(self)
        self.image_review = GroupImageReview(self.messages)
        self.image_review_service.attach_review(self.image_review)
        self.image_fallback = ImageReviewFallback(self.messages, self.delivery)
        self.messages.image_review = self.image_review
        self.messages.context_image_caption = self.context_image_caption
        self.messages.image_narration = self.image_narration
        self.messages.expression_draft_review = self.expression_draft_review
        self.messages.video_narration = self.video_narration
        self.messages.image_fallback = self.image_fallback
        self.messages.outbound_delivery = self.outbound
        self.image_review_service.attach(self)
        self.context_image_caption.attach(self)
        self.image_narration.attach(self)
        self.video_narration.attach(self)
        self.screen_interpretation.attach(self)
        self.screen_peek.attach(self)
        self.background = BackgroundCoordinator(self.tasks, self.outbound,
                                                (self.agenda_background, self.image_review_service,
                                                 self.screen_interpretation, self.screen_peek), notify=self.notify)
        self.state_maintenance.attach(self)
        self.calendar_planning.attach(self)
        self.growth_progress.attach(self)
        self.self_review.attach(self)
        self.expression_style.attach(self)
        self.expression_draft_review.attach(self)
        self.journal_writer.attach(self.gateway, require_running=self.require_running, waiting=self.background_waiting)
        self.memory_reflection.attach(self.memory, self.gateway, require_running=self.require_running)
        self.proactive.attach(self.gateway, self.outbound, require_running=self.require_running, notify=self.notify)
        self.proactive.history = self.background_history
        self.messages.proactive_inspirations = self.proactive.inspirations
        # The contact worker reads the same memory the reply path reads, so a
        # proactive body cannot contradict a stored fact. Memory stays optional:
        # an unwired host keeps generating contacts without it.
        self.proactive.attach_memory(self.memory)
        self.proactive.role_state = self.role_state
        self.proactive.life_context = self.life_context
        self.proactive.expression_context = self.expression_style_context
        self.proactive.expression_review = self.expression_draft_review
        self.contact_schedule = ContactSchedule(self.proactive, self.messages, role_life=self.role_life,
                                                role_growth=self.role_growth,
                                                role_journal=self.role_journal, calendar=self.calendar,
                                                relationships=self.relationship_memories,
                                                role_state=self.role_state, weather_port=weather_port,
                                                weather_alert_port=weather_alert_port, balance_port=balance_port,
                                                news_port=news_port, screen_peek=self.screen_peek, wake_port=wake_port,
                                                health_settings=lambda context, at: self.perception_settings.capture(
                                                    context, at)["settings"],
                                                notify=self.notify)
        self.proactive.schedule = self.contact_schedule
        self.role_reader.attach(self.gateway, self.role_reading, require_running=self.require_running,
                                waiting=self.background_waiting)
        self.journal_triggers = JournalTriggers(self.journal_writer, self.journal_material_collector,
                                                require_running=self.require_running)
        self.journal_schedule = JournalScheduleDriver(self.journal_triggers, notify=self.notify)
        self.memory_schedule = MemorySchedule(self.memory_reflection, notify=self.notify)
        self.life_runtime = RoleLifeCoordinator(self)
        self.journal_schedule.invoke = self.life_runtime.scheduled_diary
        self.diagnostics = DiagnosticService(self.configuration, now=self.now, tasks=self.tasks,
                                             models=self.gateway, messages=self.messages)
        if external is not None:
            external.attach(self.messages)
            if {"game.tictactoe.move", "game.gomoku.move", "game.pig_dice.roll",
                    "game.pig_dice.hold", "game.blackjack.create", "game.blackjack.hit",
                    "game.blackjack.stand", "game.xiangqi.create",
                    "game.xiangqi.move"} & external.actions:
                self.memory.attach_game(external, require_running=self.require_running)
        self.tools = LocalRoleTools(self.messages, diagnostics=self.diagnostics, external=external, memory=self.memory,
                                   growth=self.role_growth_context.reader, timeline=self.role_timeline, calendar=self.calendar,
                                   relationships=self.relationship_memories, weather_port=weather_port,
                                   role_state=self.role_state, authoring=self.authoring,
                                   web_search_factory=search_port_factory)
        self.messages.reply_settings = self.reply_settings
        self.messages.perception_settings = self.perception_settings
        self.acquaintance_settings = AcquaintanceSettings(self.configuration)
        self.messages.acquaintance.settings = self.acquaintance_settings
        self.role_attention.settings = self.acquaintance_settings
        self.cross_window = RoleAwareness(runtime, acquaintance=self.messages.acquaintance,
                                          source_validator=self.messages.sources)
        self.messages.cross_window = self.cross_window
        self.messages.cross_window_context = ConversationAwarenessContext(self.cross_window)
        self.generator = LocalReplyGenerator(self.messages, self.gateway, split=split, segmentation=segmentation,
                                             voice_policy=voice_policy)
        self.flow = ConversationFlow(self.messages, self.delivery, policy=policy)
        self.runner = ExactTaskRunner(self.tasks, frozenset({REPLY_KIND, DELIVERY_KIND}), self._execute)
        from companion_v2.conversation.streaming_reply import send_stream_part

        async def stream_sender(run, part_id, settings):
            await send_stream_part(self.delivery, self.runner, run, part_id, settings)

        if limits.foreground_slots >= 2:
            self.generator.send_stream_part = stream_sender
        self.state = "new"
        self.failure_code = None
        self.failure_detail = None
        self._wake = asyncio.Event()
        self._driver = None
        self._wal_checkpoint_worker = None
        self._workers = {}
        self._storage_closed = False
        self._closing = asyncio.Lock()
        from companion_v2.delivery.presence import PresenceCoordinator
        self.presence = PresenceCoordinator(self, presence_port) if presence_port is not None else None

    def require_running(self):
        if self.state != "running":
            raise TaskError("host_not_running", "host intake is not active")
        require(self.tasks.check_generation())

    def _record_failure(self, error, fallback):
        self.failure_code = error.code if isinstance(error, TaskError) else fallback
        frames = traceback.extract_tb(error.__traceback__)[-10:]
        location = " > ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames)
        self.failure_detail = {"code": self.failure_code, "error_type": type(error).__name__, "location": location}
        logger.error("[CompanionV2] 宿主运行失败 code=%s error_type=%s location=%s",
                     self.failure_code, type(error).__name__, location)

    def attach_semantics(self, port, *, reaction_role_count=0):
        from companion_v2.expression.semantic_service import SemanticService
        if self.semantics is not None:
            raise ValueError("semantic service already attached")
        self.semantics = SemanticService(self, port, reaction_role_count=reaction_role_count)
        self.tools.semantic = self.semantics
        self.tools.actions |= self.semantics.actions
        self.messages.semantics = self.semantics
        self.proactive.attach_semantics(self.semantics)
        if self.state == "running":
            self._reconcile_contact_reactions()
        return self.semantics

    def attach_laya_decision(self, resolver):
        """Attach the optional semantic decision resolver supplied by the host."""
        if resolver is not None and not callable(resolver):
            raise ValueError("Laya decision resolver must be callable or None")
        self.laya_decision_resolver = resolver
        self.messages.laya_decision_resolver = resolver

    async def start(self, *, expected_task_generation, expected_model_generation):
        async with self._closing:
            return await self._start(expected_task_generation, expected_model_generation)

    async def _start(self, expected_task_generation, expected_model_generation):
        if self.state != "new":
            return Outcome.failed("host_already_started", "assembly has a single startup lifecycle")
        self.state = "starting"
        try:
            # Neither generation can be taken over if the other CAS fails.
            with self.runtime.transaction():
                generation = require(self.tasks.start(expected_generation=expected_task_generation))
                model_generation = self.router.start(expected_generation=expected_model_generation)
                self.proactive.start()
        except (TaskError, ConfigurationError) as error:
            self.tasks.generation = self.router.generation = None
            self.configuration._active_generations.pop(CONSUMER, None)
            self.configuration._active_generations.pop(CONTACT_GROUP, None)
            self.state = "new"
            return Outcome.failed(error.code, str(error))
        except Exception:
            self.tasks.generation = self.router.generation = None
            self.configuration._active_generations.pop(CONSUMER, None)
            self.configuration._active_generations.pop(CONTACT_GROUP, None)
            self.state = "new"
            return Outcome.failed("host_startup_failed", "startup transaction failed; intake remains closed")
        try:
            recovered = unresolved = released_slots = 0
            while True:
                page = require(self.tasks.recover(limit=32))
                recovered += len(page)
                if len(page) < 32:
                    break
                await asyncio.sleep(0)
            after_attempt_id = None
            while True:
                page = require(self.tasks.recover_stopped_workers(limit=32, after_attempt_id=after_attempt_id))
                released_slots += len(page.released_tasks)
                if page.scanned < 32:
                    break
                after_attempt_id = page.last_attempt_id
                await asyncio.sleep(0)
            # Original model reservations are fenced by attempt generation, so they
            # are settled here rather than by the task recovery above. Anything a
            # dead process left behind, or any call that ended without a usage
            # receipt, is returned to its account once it clearly outlived its task.
            recovered += self.gateway.ledger.recover(self.tasks)
            self.gateway.ledger.reclaim_expired(self.tasks, limit=1000)
            while True:
                page = self.background_waiting.recover(limit=32)
                recovered += len(page)
                if len(page) < 32:
                    break
                await asyncio.sleep(0)
            while True:
                page = require(self.delivery.recover_dispatches(limit=32))
                unresolved += len(page)
                if len(page) < 32:
                    break
                await asyncio.sleep(0)
            while True:
                page = require(self.outbound.recover_dispatches(limit=32))
                unresolved += len(page)
                if len(page) < 32:
                    break
                await asyncio.sleep(0)
            require(self.tasks.check_generation())
            self.state = "running"
            self._reconcile_contact_reactions()
            self.memory_schedule.start_recovery()
            self._driver = asyncio.create_task(self._drive(), name="companion:conversation-host")
            self.notify()
            return Outcome.success(StartupReport(generation, model_generation, recovered, unresolved, released_slots))
        except asyncio.CancelledError:
            self.state = "faulted"
            self.provider_registry.close()
            self.failure_code = "startup_cancelled"
            raise
        except Exception as error:
            self.state = "faulted"
            self.provider_registry.close()
            self._record_failure(error, "host_recovery_failed")
            return Outcome.failed(self.failure_code, "host recovery did not complete; intake remains closed")

    @outcome
    def accept(self, context, event, *, role_instruction):
        self.require_running()
        # 私聊对话权限（按人）：没有权限就不进对话。回话由宿主层的准入检查负责
        # （那里有平台路由、且在收下消息之前），这里只兜底判定，避免同一条回两次。
        self.private_access_gate.guard(context)
        batch_id = require(self.flow.accept(context, event, role_instruction=role_instruction,
                                            defer_seconds=max(self.busy_deferral(context, event),
                                                              self.rest_deferral(context, event))))
        if context.scope.kind is ScopeKind.GROUP:
            self.notify()
            return batch_id
        # Schedule persistence is optional for reply admission and can wait until
        # the intake transaction has committed.
        self.memory_schedule.schedule_configuration(context, role_instruction)
        try:
            self.state_maintenance.bind(context, role_instruction)
            if self.presence is not None:
                self.presence.bind(context)
        except (TaskError, ValueError, KeyError):
            self.state_maintenance.results["configuration"] = {"state": "blocked", "code": "state_configuration"}
        self.notify()
        return batch_id

    def busy_deferral(self, context, event):
        """How long this private window delays its own passive reply.

        The delay is judged from the role's accepted schedule and is written into
        the durable batch timer, so a restart keeps the same wait and no intake
        coroutine ever sleeps. An unreadable schedule or source never blocks an
        accepted message: the reply simply keeps its normal timing.
        """
        if context.scope.kind is not ScopeKind.PRIVATE:
            return 0.0
        try:
            record = self.messages._read_source(event.source, context, event.capability, self.now())
            text = record.payload if record.content_hash == event.input_hash else ""
            return busy_gate.deferral(self.runtime, context, text=text, now=self.now())
        except (TaskError, ValueError, KeyError, TypeError):
            return 0.0

    def rest_deferral(self, context, event):
        """How long this private window holds a reply while the role is resting.

        The rest gate decides inside its own sleep window whether to answer now;
        a held input is deferred through the same durable timer as the busy gate
        and becomes visible again as a bounded backlog once the role wakes. An
        unreadable sleep receipt or source never blocks an accepted message.
        """
        if context.scope.kind is not ScopeKind.PRIVATE:
            return 0.0
        try:
            record = self.messages._read_source(event.source, context, event.capability, self.now())
            text = record.payload if record.content_hash == event.input_hash else ""
            return rest_gate.deferral(self.runtime, context, text=text, now=self.now(),
                                      event_key=event.scoped_key, source=event.source,
                                      capability=event.capability,
                                      body_clock=self.role_state.body_clock)
        except (TaskError, ValueError, KeyError, TypeError):
            return 0.0

    @outcome
    def platform_recall(self, notice):
        self.require_running()
        result = require(self.messages.recalls.accept(notice))
        if self.presence is not None and result.get("cancelled", 0):
            self.presence.stop_native_scope(
                notice.platform, notice.account_id, notice.kind, notice.native_scope_id)
        self.notify()
        return result

    @outcome
    def withdraw(self, context, event_key):
        self.require_running()
        batch_id = require(self.messages.withdraw(context, event_key))
        self.notify()
        return batch_id

    @outcome
    def cancel(self, context, batch_id):
        self.require_running()
        result = require(self.flow.cancel(context, batch_id))
        self.notify()
        return result

    @outcome
    def reset_conversation_window(self, window, *, reset_id):
        from companion_v2.conversation.reset import reset_window

        result = reset_window(self.runtime, window, reset_id=reset_id, at=self.now())
        batches = set(result["batches"])
        for work, worker in tuple(self._workers.values()):
            if work.batch_id in batches:
                worker.cancel()
        participation = getattr(self.tools, "group_participation", None)
        if participation is not None:
            if not result["replayed"]:
                participation.admission.reset_window(window)
            for batch_id in batches:
                participation._frozen_turns.pop(batch_id, None)
        if self.state == "running":
            for batch_id in batches:
                for task_id in self.flow._batch_tasks(batch_id):
                    require(self.tasks.discard_queued(task_id, reason="owner_closed"))
                self.flow._resolve(batch_id, "context_reset", expected=False)
        self.notify()
        return result

    def record_receipt(self, receipt):
        # This trusted accounting hook remains usable during draining, while storage is held.
        if self._storage_closed:
            return Outcome.failed("host_storage_closed", "receipt owner storage has been closed")
        owner = self.outbound if self.outbound.contains(receipt.part_id) else self.delivery
        result = owner.record_receipt(receipt)
        if owner is self.delivery and result.is_success and result.unwrap() == "delivered":
            self._mark_reaction_sent(receipt.part_id)
        self.notify()
        return result

    @outcome
    def reconcile_delivery(self, context, part_id):
        self.require_running()
        result = require(self.outbound.reconcile_task(context, part_id) if self.outbound.contains(part_id)
                         else self.flow.reconcile_delivery(context, part_id))
        if self.outbound.contains(part_id):
            self._mark_contact_sent(part_id)
        else:
            self._mark_reaction_sent(part_id)
        self.notify()
        return result

    def _mark_reaction_sent(self, part_id):
        if self.semantics is None:
            return
        try:
            if self.semantics.reactions.mark_sent(part_id):
                self.semantics.errors.pop("reaction_usage", None)
        except Exception as exc:
            # A confirmed platform receipt remains valid if accounting fails.
            self.semantics.errors["reaction_usage"] = getattr(exc, "code", type(exc).__name__)

    def _mark_contact_sent(self, delivery_id):
        self.proactive.delivery_confirmed(delivery_id)
        if self.semantics is not None:
            try:
                if self.semantics.reactions.mark_contact_sent(delivery_id):
                    self.semantics.errors.pop("reaction_usage", None)
            except Exception as exc:
                self.semantics.errors["reaction_usage"] = getattr(exc, "code", type(exc).__name__)
        self.notify()

    def _record_contact_delivery_receipt(self, request, receipt):
        if request.owner == CONTACT_GROUP:
            self.proactive.record_delivery_receipt(request, receipt)

    @staticmethod
    def _report_wal_checkpoint(task):
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as error:
            logger.warning("[CompanionV2] SQLite WAL checkpoint failed error_type=%s", type(error).__name__)

    def _reconcile_contact_reactions(self):
        last = ""
        while True:
            rows = self.runtime.read_all("""SELECT delivery_id FROM background_deliveries
                WHERE owner = ? AND state = 'delivered' AND delivery_id > ?
                ORDER BY delivery_id LIMIT 32""", (CONTACT_GROUP, last))
            for row in rows:
                self.proactive.delivery_confirmed(row["delivery_id"])
                if self.semantics is not None:
                    self._mark_contact_sent(row["delivery_id"])
            if len(rows) < 32:
                break
            last = rows[-1]["delivery_id"]

    def notify(self):
        """Wake the owning loop after a committed change, including worker writes."""
        loop = self._driver.get_loop() if self._driver is not None else None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if loop is not None and loop is not current_loop:
            # SQLite observations also notify from a worker. Event.set() must
            # run on its loop so waiter callbacks and cancellation stay valid.
            try:
                loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                if not loop.is_closed():
                    raise
            return
        self._wake.set()

    async def _execute(self, run):
        # The supervisor remembers only that a worker stopped; the turn's own
        # reply diagnosis needs the code the owner actually raised.
        lease, attention_note = None, ""
        try:
            if run.task.spec.kind == REPLY_KIND:
                attention = getattr(self, "role_attention", None)
                if attention is not None:
                    context = run.task.spec.context
                    lease = await attention.acquire(context, run.task.spec.task_id,
                                                    window=context.scope.kind.value)
                    attention_note = attention.note(lease)
                result = await self.generator.generate(run, attention_note=attention_note)
                if self.semantics is not None:
                    self.semantics.schedule_memory(run.task.spec.context)
                return result
            result = await self.delivery.deliver(run)
        except TaskError as error:
            self.flow.note_failure(run.task.task_id, error.code)
            raise
        finally:
            if lease is not None:
                self.role_attention.finish(lease)
        if result.result_ref:
            self._mark_reaction_sent(result.result_ref)
        return result

    async def _drive(self):
        try:
            loop = asyncio.get_running_loop()
            next_contact_delivery_check = 0.0
            next_wal_checkpoint = loop.time()
            next_budget_reclaim = loop.time() + self.BUDGET_RECLAIM_INTERVAL_SECONDS
            while self.state == "running":
                self._wake.clear()
                require(self.tasks.check_generation())
                for task_id, (work, worker) in tuple(self._workers.items()):
                    if worker.done():
                        del self._workers[task_id]
                        if not worker.cancelled():
                            self.flow.completed(work.batch_id, worker.result())
                excluded = tuple(work.batch_id for work, _ in self._workers.values())
                available = self.worker_limit - len(self._workers)
                if available > 0:
                    def slots_for(work):
                        batch = self.messages.batch(work.batch_id)
                        saved = json.loads(batch["contributions_json"]).get("reply_settings", {})
                        values = saved.get("settings", {})
                        return 2 if (batch["generation_task_id"] == work.task_id and values.get("enabled", False)
                            and values.get("streaming_mode", "buffered") == "incremental"
                            and values.get("streaming", False) and self.tasks.limits.foreground_slots >= 2) else 1

                    used = sum(slots_for(work) for work, _ in self._workers.values())
                    for work in require(self.flow.advance(limit=available, excluded=excluded)):
                        slots = slots_for(work)
                        if used + slots > self.tasks.limits.foreground_slots:
                            self.flow.defer(work.batch_id)
                            continue
                        used += slots
                        worker = asyncio.create_task(self.runner.run(work.context, work.task_id),
                                                     name="companion:flow:" + work.batch_id)
                        self._workers[work.task_id] = (work, worker)
                        worker.add_done_callback(lambda done: self.notify())
                # Receipt notifications first release completed delivery workers
                # and admit due parts. Planners run below, after send work starts.
                more_background = self.background.advance(planners=False)
                await asyncio.sleep(0)
                excluded = tuple(work.batch_id for work, _ in self._workers.values())
                deadline = self.flow.next_wake(excluded=excluded) if len(self._workers) < self.worker_limit else None
                self.memory_schedule.advance()
                memory_deadline = self.memory_schedule.next_wake()
                if memory_deadline is not None:
                    deadline = min(deadline, memory_deadline) if deadline else memory_deadline
                self.life_runtime.advance()
                await asyncio.sleep(0)
                self.state_maintenance.advance()
                await asyncio.sleep(0)
                self.memory.activity.advance_bound(self.state_maintenance.bindings.values())
                if self.memory.game is not None:
                    self.memory.game.advance_bound(self.state_maintenance.bindings.values())
                self.calendar_planning.advance()
                await asyncio.sleep(0)
                self.growth_progress.advance()
                self.self_review.advance()
                await asyncio.sleep(0)
                tools = self.messages.tools
                participation = getattr(tools, "group_participation", None) if tools is not None else None
                if participation is not None:
                    participation.advance_reflection()
                self.expression_style.advance()
                await asyncio.sleep(0)
                if self.presence is not None:
                    self.presence.advance()
                if loop.time() >= next_contact_delivery_check:
                    self.proactive.advance_deliveries()
                    next_contact_delivery_check = loop.time() + 1.0
                self.journal_schedule.advance()
                journal_deadline = self.journal_schedule.next_wake()
                if journal_deadline is not None:
                    deadline = min(deadline, journal_deadline) if deadline else journal_deadline
                more_background = self.background.advance() or more_background
                await asyncio.sleep(0)
                await self.contact_schedule.advance_async()
                await asyncio.sleep(0)
                if loop.time() >= next_wal_checkpoint:
                    checkpoint = getattr(self.runtime, "checkpoint_passive", None)
                    worker = self._wal_checkpoint_worker
                    if callable(checkpoint) and (worker is None or worker.done()):
                        worker = asyncio.create_task(asyncio.to_thread(checkpoint),
                                                     name="companion:sqlite-wal-checkpoint")
                        worker.add_done_callback(self._report_wal_checkpoint)
                        self._wal_checkpoint_worker = worker
                    next_wal_checkpoint = loop.time() + self.WAL_CHECKPOINT_INTERVAL_SECONDS
                if loop.time() >= next_budget_reclaim:
                    # Maintenance, not a request path: a superseded or shutting-down
                    # controller simply leaves reclamation to the next startup.
                    try:
                        self.gateway.ledger.reclaim_expired(self.tasks)
                    except TaskError as error:
                        logger.debug("[CompanionV2] 模型过期预留回收跳过 code=%s", error.code)
                    next_budget_reclaim = loop.time() + self.BUDGET_RECLAIM_INTERVAL_SECONDS
                delivery_deadline = self.background.next_wake()
                if delivery_deadline is not None:
                    deadline = min(deadline, delivery_deadline) if deadline else delivery_deadline
                timeout = max(0, (deadline - self.now()).total_seconds()) if deadline else None
                timeout = 0 if more_background else min(timeout, 1.0) if timeout is not None else 1.0
                if timeout == 0:
                    await asyncio.sleep(0)  # Bounded pages yield to intake, cancellation and other routes.
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state = "faulted"
            self._record_failure(error, "host_dispatch_failed")
            # A failed coordinator closes intake and fences its live wrappers. It never retries effects.
            for _, worker in self._workers.values():
                worker.cancel()
            self.journal_schedule.cancel_running()
            self.memory_schedule.cancel_running()
            self.journal_triggers.cancel_running()
            self.life_runtime.cancel_running()
            self.state_maintenance.cancel_running()
            self.calendar_planning.cancel_running()
            self.growth_progress.cancel_running()
            self.self_review.cancel_running()
            self.expression_style.cancel_running()
            self.image_review_service.cancel_running()
            self.screen_interpretation.cancel_running()
            self.screen_peek.cancel_running()
            self.memory.activity.close()
            if self.memory.game is not None:
                self.memory.game.close()
            if self.presence is not None:
                self.presence.cancel_running()
            self.background.cancel()
            self.proactive.cancel_running()
            self.contact_schedule.cancel_running()
            self.provider_registry.close()

    @property
    def active_handles(self):
        return ((self.semantics.active_handles if self.semantics else ()) + self.runner.active_handles + self.journal_writer.active_handles + self.memory_reflection.active_handles
                + self.role_reader.active_handles + self.life_runtime.active_handles
                + self.state_maintenance.active_handles + self.calendar_planning.active_handles
                + self.growth_progress.active_handles
                + self.self_review.active_handles
                + self.expression_style.active_handles
                + self.expression_draft_review.active_handles
                + (self.presence.active_handles if self.presence else ())
                + self.background.active_handles + self.memory_schedule.active_handles
                + self.proactive.active_handles + self.contact_schedule.active_handles
                + self.journal_triggers.active_handles + self.journal_schedule.active_handles
                + self.image_review_service.active_handles
                + self.image_narration.active_handles
                + self.video_narration.active_handles
                + self.poke_reply_model.active_handles + tuple(self.poke_context_tasks)
                + self.screen_interpretation.active_handles
                + self.screen_peek.active_handles
                + (self.authoring.runner.active_handles if self.authoring.runner else ())
                + (self.external.runner.active_handles if self.external else ()))

    async def close(self, *, grace_seconds=0.1):
        if type(grace_seconds) not in (int, float) or not 0 <= grace_seconds <= 2:
            raise ValueError("shutdown grace must be bounded")
        async with self._closing:
            if self.state == "stopped":
                return ShutdownReport(self.state, (), self._storage_closed)
            self.state = "draining"
            if self.semantics is not None:
                await self.semantics.close()
            self.provider_registry.close()
            if self._driver is not None:
                self._driver.cancel()
                await asyncio.gather(self._driver, return_exceptions=True)
            if self._wal_checkpoint_worker is not None:
                await asyncio.gather(self._wal_checkpoint_worker, return_exceptions=True)
                self._wal_checkpoint_worker = None
            workers = tuple(worker for _, worker in self._workers.values())
            if workers:
                _, pending = await asyncio.wait(workers, timeout=grace_seconds)
                for worker in pending:
                    worker.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=0.25)
            await self.runner.close()
            if self.poke_context_tasks:
                await asyncio.wait(tuple(self.poke_context_tasks), timeout=1.0)
            await self.poke_reply_model.close()
            await self.authoring.close()
            await self.background.close(grace_seconds=grace_seconds)
            await self.life_runtime.close()
            await self.state_maintenance.close()
            await self.calendar_planning.close()
            await self.growth_progress.close()
            await self.self_review.close()
            await self.expression_style.close()
            await self.expression_draft_review.close()
            await self.image_review_service.close()
            await self.context_image_caption.close()
            await self.image_narration.close()
            await self.video_narration.close()
            await self.screen_interpretation.close()
            await self.screen_peek.close()
            self.memory.activity.close()
            if self.memory.game is not None:
                self.memory.game.close()
            if self.presence is not None:
                await self.presence.close()
            await self.role_reader.close()
            await self.journal_schedule.close()
            await self.memory_schedule.close()
            await self.journal_triggers.close()
            await self.journal_writer.close()
            await self.memory_reflection.close()
            await self.proactive.close()
            await self.contact_schedule.close()
            if self.external is not None:
                await self.external.close()
            for task_id, (_, worker) in tuple(self._workers.items()):
                if worker.done():
                    if not worker.cancelled():
                        worker.exception()
                    del self._workers[task_id]
            handles = self.active_handles
            if not handles and not self._workers and not self.background.pending:
                self.state = "stopped"
                if self.owns_runtime:
                    self.runtime.close()
                    self._storage_closed = True
            return ShutdownReport(self.state, handles, self._storage_closed)
