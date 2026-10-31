"""Explicit single-target AstrBot assembly; never a plugin entry point.

The caller supplies an isolated V2 store and already provisioned authority. This
loader cannot create identities/grants, discover V1 data, or register event handlers.
Native types come from the host. Old ``delivery_mode`` values only reproduce stored
configuration fingerprints; every live session uses the OneBot sender.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace


from companion_v2.adapters.astrbot.memory_owner import memory_owner_provider
from companion_v2.adapters.astrbot.memory_push import memory_push_provider
from companion_v2.adapters.astrbot.onebot import OneBotPrivateSender
from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.reply_settings import expression_prompt_sections
from companion_v2.conversation.voice_reply import VoiceReplyDelivery
from companion_v2.conversation.messages import REPLY_ACTION, REPLY_KIND
from companion_v2.conversation.pokes import poke_reply_profile
from companion_v2.execution.models import Lane, ProviderBinding, SupervisorLimits, TaskContext, TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.conversation.content.authoring_tasks import authoring_profiles
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.model_gateway.budget import BudgetSoftPolicy
from companion_v2.model_gateway.models import BudgetPolicy, MAX_MODEL_OUTPUT_TOKENS, ModelTaskProfile
from companion_v2.role_work.journal.writing import writing_profiles
from companion_v2.proactive.service import contact_profile, persona_judge_profile
from companion_v2.role_work.reader import reading_profile
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.memory.reflection import reflection_profile
from companion_v2.role_work.state.maintenance import state_profile
from companion_v2.agenda.planning import planning_profile
from companion_v2.role_work.growth.progress import growth_progress_profile
from companion_v2.role_work.journal.self_review_service import self_review_profile
from companion_v2.expression.style_service import expression_learning_profile
from companion_v2.expression.review import expression_review_profile
from companion_v2.screen.interpretation import screen_interpretation_profile
from companion_v2.conversation.media.image_narration import image_narration_profile
from companion_v2.conversation.media.video.narration import video_narration_profile
from companion_v2.conversation.media.understanding import intake_settings as file_intake_settings
from companion_v2.conversation.relay_settings import configuration_fields as relay_configuration_fields
from .presence import OneBotPresencePort
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .application import AstrBotCompanionHost
from .tools import CompositeAstrBotToolPort
from .native import HostAccount, HostPersona, NativePrivateBridge, check_version, persona_instruction, route_id
from .provider import AstrBotTextProvider, REPLY_TASK_CAPABILITIES, UsageTariff, provider_fingerprint
from .provider_catalog import TextProviderSettings, assemble_providers, loaded_routing
from .staging_config import DELIVERY_CAPABILITY, DELIVERY_GRANT_ID
from .staging_delivery import delivery_grants, require_delivery_access


@dataclass(frozen=True)
class NativeAdapterTypes:
    version: str
    event: type
    provider: type
    function_tool: type
    tool_set: type
    reverse_api: type
    image: type | None = None
    image_loader: object | None = None
    reply: type | None = None
    quote_loader: object | None = None
    forward: type | None = None
    forward_loader: object | None = None
    audio: type | None = None
    audio_loader: object | None = None
    component_reader: object | None = None
    group_quote_loader: object | None = None
    group_forward_loader: object | None = None
    video: type | None = None
    video_loader: object | None = None
    group_component_reader: object | None = None
    file: type | None = None
    file_loader: object | None = None
    video_provider_type: type | None = None
    video_provider_factory: object | None = None
    session_persona_override_lookup: object | None = None
    persona_snapshot_lookup: object | None = None
    persona_selection_lookup: object | None = None
    text_provider_types: tuple[type, ...] = ()

    @property
    def provider_types(self):
        """Every native class the V2 text transport may wrap.

        OpenAI-compatible providers keep using ``provider``; additional classes
        (for example AstrBot's native Google GenAI provider) are listed here and
        translated by the same adapter.
        """
        return (self.provider,) + tuple(self.text_provider_types)

    def is_text_provider(self, provider):
        """Registered class or the public AstrBot text-provider surface."""
        from .provider import looks_like_text_provider
        return isinstance(provider, self.provider_types) or looks_like_text_provider(provider)

    def __post_init__(self):
        check_version(self.version)
        if any(not isinstance(item, type) for item in self.text_provider_types):
            raise ValueError("text provider types must be native provider classes")
        if (self.file is None) != (self.file_loader is None) or (self.file is not None and (
                not isinstance(self.file, type) or not callable(self.file_loader))):
            raise ValueError("file intake requires an explicit native type and loader")
        if ((self.video_provider_type is None) != (self.video_provider_factory is None)
                or self.video_provider_type is not None and (
                    not isinstance(self.video_provider_type, type) or not callable(self.video_provider_factory))):
            raise ValueError("native video provider requires an explicit type and adapter factory")
        if self.session_persona_override_lookup is not None and not callable(self.session_persona_override_lookup):
            raise ValueError("session persona override lookup must be supplied by trusted host assembly")
        if self.persona_snapshot_lookup is not None and not callable(self.persona_snapshot_lookup):
            raise ValueError("persona snapshot lookup must be supplied by trusted host assembly")
        if self.persona_selection_lookup is not None and not callable(self.persona_selection_lookup):
            raise ValueError("persona selection lookup must be supplied by trusted host assembly")
        if (self.video is None) != (self.video_loader is None) or (self.video is not None and (
                not isinstance(self.video, type) or not callable(self.video_loader))):
            raise ValueError("video intake requires an explicit native type and loader")
        if self.group_quote_loader is not None and (self.reply is None or not callable(self.group_quote_loader)):
            raise ValueError("group quote intake requires an explicit native Reply type and group loader")
        if self.group_forward_loader is not None and (self.forward is None or not callable(self.group_forward_loader)):
            raise ValueError("group forward intake requires an explicit native Forward type and group loader")
        if (self.audio is None) != (self.audio_loader is None) or (self.audio is not None and (
                not isinstance(self.audio, type) or not callable(self.audio_loader))):
            raise ValueError("audio intake requires an explicit native type and loader")
        if self.component_reader is not None and not callable(self.component_reader):
            raise ValueError("component reader must be callable")
        if self.group_component_reader is not None and not callable(self.group_component_reader):
            raise ValueError("group component reader must be callable")
        if any(not isinstance(value, type) for value in (
                self.event, self.provider, self.function_tool, self.tool_set, self.reverse_api)):
            raise ValueError("native adapter types must be supplied by trusted host assembly")
        if (self.image is None) != (self.image_loader is None) or (self.image is not None and (
                not isinstance(self.image, type) or not callable(self.image_loader))):
            raise ValueError("image intake requires an explicit native image type and loader")
        if (self.reply is None) != (self.quote_loader is None) or (self.reply is not None and (
                not isinstance(self.reply, type) or not callable(self.quote_loader))):
            raise ValueError("quote intake requires an explicit native reply type and loader")
        if (self.forward is None) != (self.forward_loader is None) or (self.forward is not None and (
                not isinstance(self.forward, type) or not callable(self.forward_loader))):
            raise ValueError("forward intake requires an explicit native type and loader")


@dataclass(frozen=True)
class StagingProfile:
    target: TaskContext
    runtime_identity: RuntimeIdentity
    persona: HostPersona
    provider_id: str
    tariff: UsageTariff
    budget: BudgetPolicy
    task_prompt: str
    budget_soft: BudgetSoftPolicy = BudgetSoftPolicy()
    delivery_mode: str = "onebot_private"
    max_output_tokens: int = 1024
    maximum_input_estimate: int = 131072
    output_parameter: str = "max_tokens"
    policy: ConversationPolicy = ConversationPolicy()
    limits: SupervisorLimits = SupervisorLimits()
    streaming: bool = False
    segmentation: SegmentationPolicy = SegmentationPolicy()
    forward_intake: ForwardIntakePolicy = ForwardIntakePolicy()

    def __post_init__(self):
        scope, principal = self.target.scope, self.target.actor.principal
        if (scope.kind is not ScopeKind.PRIVATE or scope.installation_id != principal.installation_id
                or scope.platform != principal.platform or scope.bot_id != principal.bot_id
                or scope.account_id != principal.account_id or scope.native_scope_id != principal.native_account_id
                or scope.session_id != scope.native_scope_id or principal.host_id != self.runtime_identity.host_id
                or self.runtime_identity.host_kind != "astrbot"
                or self.target.audience.recipients != (self.target.actor.subject,)
                or self.persona.bot_id != scope.bot_id or self.persona.role != self.target.role_binding.role):
            raise ValueError("staging requires one consistent private target, host, account and persona")
        route_id(scope.account_id)
        route_id(scope.native_scope_id)
        if self.delivery_mode == "preview":
            object.__setattr__(self, "delivery_mode", "onebot_private")
        elif self.delivery_mode != "onebot_private":
            raise ValueError("staging delivery is fixed to OneBot private")


class _StagingBridge(NativePrivateBridge):
    def __init__(self, *args, target, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)

    def client(self, scope):
        if scope != self.target.scope:
            raise TaskError("staging_target_mismatch", "this loader is pinned to one private conversation")
        return super().client(scope)

    async def _role_for_umo(self, account, umo):
        """Follow edits to the same selected AstrBot persona without rebinding it.

        ``NativePrivateBridge`` normally requires the prompt digest to match a
        separately trusted projection. This staging host owns the selected
        persona identity and intentionally follows its current prompt; the Bot,
        selected persona name and V2 role remain pinned and are still checked.
        """
        cid, selected, persona, special = await self._selected_persona(account, umo)
        projection = self.personas.get((account.bot_id, selected))
        if special or projection is None or (persona is None and selected not in (None, "[%None]")):
            raise TaskError("host_persona_unmapped", "selected persona needs an explicit V2 projection")
        if persona is not None and persona.get("name") != selected:
            raise TaskError("host_persona_invalid", "host returned a different persona")
        return self._conversation_key(umo, cid), projection.role, persona_instruction(persona)

    async def verify_role(self, scope, binding, instruction=None):
        if binding != self.target.role_binding:
            raise TaskError("staging_target_mismatch", "original role binding differs from the staging target")
        return await super().verify_role(scope, binding, instruction)


class AstrBotStagingSession:
    """Own the host lifecycle, but borrow host clients and the explicitly supplied store.

    A local review-only rehearsal can call the configured model without sending a
    platform message. Official sessions use the native OneBot private sender. Calling
    this constructor does no model/platform I/O and does not activate the driver.
    """

    PIN_ID = "astrbot-staging-profile"

    def __init__(self, runtime, context, *, profile: StagingProfile, native_types: NativeAdapterTypes,
                 native_tool_port=None, now=None, voice=None, voice_policy=None, weather_port=None,
                 weather_alert_port=None, balance_port=None, news_port=None, solar_term_port=None, qzone_transport=None, additional_providers=(), fallback_provider_id="", replacement_rules="[]",
                 fallback_on_refusal=False, review_only=False, profile_field_shapes=None,
                 bootstrap_receipt_validated=False, default_provider_resolver=None,
                 relay_tool_port_factory=None, search_port_factory=None):
        if runtime.installation_id != profile.target.scope.installation_id:
            raise ValueError("staging storage and target must have the same installation")
        self.runtime, self.profile, self.native_types = runtime, profile, native_types
        # 上一次"因为没有对话权限而被拒"的对象；控制器据此在事务之外回一句。
        self.last_refusal = None
        self.review_only = bool(review_only)
        self.bootstrap_receipt_validated = bool(bootstrap_receipt_validated)
        self._uses_local_sender = self.review_only
        self.native_tool_port = native_tool_port
        target, scope = profile.target, profile.target.scope
        self.bridge = _StagingBridge(context, target=target, version=native_types.version,
            event_type=native_types.event, installation_id=runtime.installation_id,
            host_id=profile.runtime_identity.host_id, identities=PersistentIdentityDirectory(runtime),
            accounts=(HostAccount(scope.platform, scope.account_id, scope.bot_id),), personas=(profile.persona,),
            image_type=native_types.image, image_loader=native_types.image_loader,
            reply_type=native_types.reply, quote_loader=native_types.quote_loader,
            forward_type=native_types.forward, forward_loader=native_types.forward_loader, now=now,
            audio_type=native_types.audio, audio_loader=native_types.audio_loader, component_reader=native_types.component_reader,
            video_type=native_types.video, video_loader=native_types.video_loader,
            forward_intake=profile.forward_intake,
            file_type=native_types.file, file_loader=native_types.file_loader,
            session_persona_override_lookup=native_types.session_persona_override_lookup,
            persona_snapshot_lookup=native_types.persona_snapshot_lookup,
            persona_selection_lookup=native_types.persona_selection_lookup,
            file_settings=lambda scope, role: file_intake_settings(runtime, scope, role))
        self.identity = PersistentIdentityDirectory(runtime)
        # These identities are stable across a restart of the same native config.
        native = context.get_provider_by_id(profile.provider_id)
        if not native_types.is_text_provider(native):
            raise ValueError("staging provider is unavailable or has an unverified native type")
        binding = ProviderBinding("model.text", profile.provider_id, native_types.version,
            provider_fingerprint(native))
        self.model = AstrBotTextProvider(context, bridge=self.bridge, binding=binding,
            provider_id=profile.provider_id, version=native_types.version, provider_type=native_types.provider_types,
            function_tool_type=native_types.function_tool, tool_set_type=native_types.tool_set,
            tariff=profile.tariff, maximum_output_tokens=MAX_MODEL_OUTPUT_TOKENS,
            maximum_input_estimate=profile.maximum_input_estimate, output_parameter=profile.output_parameter,
            streaming=profile.streaming)
        if self.model.native is not native:
            raise TaskError("host_provider_changed", "native provider changed during staging assembly")
        self.model._current()
        self.models = (self.model,) + assemble_providers(context, bridge=self.bridge, native_types=native_types,
            primary_id=profile.provider_id, additional=additional_providers,
            defaults=TextProviderSettings(profile.provider_id, profile.tariff, MAX_MODEL_OUTPUT_TOKENS,
                profile.maximum_input_estimate, profile.output_parameter, profile.streaming))
        fallback_provider_id, replacement_rules = loaded_routing(fallback_provider_id, replacement_rules,
            (model.binding.provider for model in self.models))
        narration_profiles = tuple(image_narration_profile(model.binding.provider)
            for model in self.models if "image" in model.local_provider().capabilities)[:1]
        native_video_profiles = tuple(video_narration_profile(model.binding.provider)
            for model in self.models if "video" in model.local_provider().capabilities)[:1]
        self.model_profile = ModelTaskProfile(REPLY_KIND, Lane.FOREGROUND, profile.provider_id,
            profile.task_prompt, default_fallback=fallback_provider_id, replacement_rules=replacement_rules,
            fallback_on_refusal=fallback_on_refusal,
            max_output_tokens=profile.max_output_tokens, capabilities=REPLY_TASK_CAPABILITIES,
            prompt_sections=expression_prompt_sections())
        self.writing_profiles = writing_profiles(profile.provider_id, max_output_tokens=profile.max_output_tokens)
        self.reading_profile = reading_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens)
        profiles = (self.model_profile, poke_reply_profile(profile.provider_id,
                    max_output_tokens=min(128, profile.max_output_tokens)),
                    contact_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    persona_judge_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    reflection_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    state_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    planning_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    growth_progress_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    self_review_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    expression_learning_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    expression_review_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens),
                    expression_review_profile(profile.provider_id, max_output_tokens=profile.max_output_tokens, contact=True),
                    screen_interpretation_profile(profile.provider_id), *narration_profiles,
                    *native_video_profiles) + self.writing_profiles + (self.reading_profile,) + authoring_profiles(
                        profile.provider_id, draft_tokens=profile.max_output_tokens)
        registry_fields = configuration_fields(profiles)
        registry_ids = {item.field_id for item in registry_fields}
        registry_fields += tuple(field for field in relay_configuration_fields()
                                 if field.field_id not in registry_ids)
        registry = FieldRegistry(version="astrbot-staging.v1", fields=registry_fields)
        self.sender_binding = ProviderBinding("message.delivery", "astrbot-staging.onebot_private",
            native_types.version, digest([target.key, profile.persona, "onebot_private"]))
        if self._uses_local_sender:
            sender = LocalSender(self.sender_binding, self._preview, supports_images=True)
        else:
            sender_type = (OneBotPrivateSender
                           if self.bridge.platform_name(target.scope) == "aiocqhttp"
                           else AstrBotPlatformSender)
            sender = sender_type(self.bridge, binding=self.sender_binding, version=native_types.version,
                reverse_api_type=native_types.reverse_api, authorize=self.require_delivery_authorization,
                now=now, voice=voice,
                voice_delivery=VoiceReplyDelivery(runtime, now) if voice is not None else None,
                reaction_image_format=lambda: self.application.semantics.store.settings()["reactions_image_format"]
                    if self.application.semantics is not None else "image").local_sender()
        tool_port = native_tool_port
        if relay_tool_port_factory is not None:
            relay_port = relay_tool_port_factory(self.bridge)
            tool_port = CompositeAstrBotToolPort(native_tool_port, relay_port)
        self.host = AstrBotCompanionHost(runtime, event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, runtime_identity=profile.runtime_identity, registry=registry,
            profiles=profiles, providers=tuple(model.local_provider() for model in self.models), budget=profile.budget,
            budget_soft=profile.budget_soft,
            default_provider_resolver=default_provider_resolver,
            sender=sender, policy=profile.policy, limits=profile.limits, now=now,
            tool_port=tool_port, segmentation=profile.segmentation, voice_policy=voice_policy,
            # 记忆归属读的是别家插件自己公开的声明；适配层的这一个函数是唯一的跨插件
            # 读取点（核心不能反向导入宿主包，见 companion_v2/capabilities/ownership.py）。
            # 写入端口与它共用同一条发现路径和同一个 context：日记、梦境与主动消息
            # 完成后推给它，推送失败不影响已经完成的事（见 capabilities/memory_push.py）。
            memory_owner=memory_owner_provider(context),
            memory_push=memory_push_provider(context),
            weather_port=weather_port, weather_alert_port=weather_alert_port, balance_port=balance_port,
            solar_term_port=solar_term_port,
            news_port=news_port,
            search_port_factory=search_port_factory,
            qzone_transport=qzone_transport,
            presence_port=OneBotPresencePort(self.bridge, reverse_api_type=native_types.reverse_api,
                authorize=self.require_delivery_authorization, now=lambda: self.application.now())
                if not self._uses_local_sender else None)
        self.application = self.host.application
        if callable(getattr(native_tool_port, "bind_role_guard", None)):
            native_tool_port.bind_role_guard(self.bridge.verify_role, self.bridge.verified_persona)
        self.config_target = ConfigTarget(runtime.installation_id, ConfigLayer.WINDOW, target.role_binding.role, scope)
        self._lifecycle = asyncio.Lock()
        self._intake = asyncio.Lock()
        self._starting = False
        # Store only a digest, never native credentials, prompts or provider configuration.
        #
        # This is the *identity* of the profile, not its tuning. It answers "is this the same
        # role, talking to the same person, through the same account and the same provider",
        # and deliberately leaves out everything that legitimately moves underneath it: the
        # provider's configuration fingerprint, its tariff and output limits, the budget, the
        # policy, the limits, streaming and segmentation.
        #
        # Why: this gate used to hash all of that, so a model renamed upstream or a re-tuned
        # budget refused to reopen the store and took the whole role offline, while the
        # operator saw only "connection failed". None of that rebinds anything.
        #
        # What still refuses: a different target or persona, a different account, or a
        # different provider *id*. The provider's configuration is checked separately --
        # see the provider fingerprint checks in ``staging_bootstrap`` -- so dropping the
        # fingerprint here does not let a reconfigured provider through; it only stops it
        # being reported as a binding conflict.
        identity_binding = {"capability": binding.capability, "provider": binding.provider,
                            "version": binding.version}
        self.profile_hash = digest({"target": target.key, "host": profile.runtime_identity.host_id,
            "ecosystem": profile.runtime_identity.ecosystem_id, "persona": profile.persona,
            "model": identity_binding, "sender": self.sender_binding})
        # Earlier writers pinned the full assembly. Keep reproducing that known
        # format for direct adapter callers; host sessions rely on the separately
        # validated bootstrap receipt so this coarse legacy hash cannot reject
        # harmless configuration drift.
        shapes = profile_field_shapes or {}
        policy = asdict(profile.policy)
        segmentation = asdict(profile.segmentation)
        policy = {key: policy[key] for key in shapes.get("policy", policy)}
        segmentation = {key: segmentation[key] for key in shapes.get("segmentation", segmentation)}
        legacy_model = asdict(replace(self.model_profile, default_fallback="", replacement_rules="[]"))
        for name in ("replacement_rules", "fallback_on_refusal", "timeout_seconds", "replacement_scope", "prompt_sections"):
            legacy_model.pop(name)
        self._legacy_profile_hash = digest({"target": target.key, "host": profile.runtime_identity.host_id,
            "ecosystem": profile.runtime_identity.ecosystem_id, "persona": profile.persona,
            "model": binding, "model_profile": legacy_model, "tariff": profile.tariff,
            "input_limit": profile.maximum_input_estimate, "output_parameter": profile.output_parameter,
            "sender": self.sender_binding, "budget": profile.budget, "policy": policy, "limits": profile.limits,
            **({"streaming": True} if profile.streaming else {}),
            **({"segmentation": segmentation} if profile.segmentation != SegmentationPolicy() else {})})

    def _configuration(self):
        app, target = self.application, self.profile.target
        if target.scope.kind is ScopeKind.PRIVATE:
            # 私聊：对话权限按人判定。没有权限时不进对话；回话由控制器在事务之外发
            # （那里才有平台客户端），这里只记下对象。
            if app.private_access_gate.denied(target, at=app.now()):
                self.last_refusal = target
                raise TaskError("private_access_dialogue_denied",
                                "this private user has no dialogue permission")
        else:
            require(app.tasks.check_context_access(target, action=REPLY_ACTION, observed_at=app.now()))
        if app.tasks.generation is not None:
            require(app.tasks.check_generation())
        snapshot = require(app.configuration.read(target.actor, target=self.config_target,
            group_id=self.model_profile.group, observed_at=app.now()))
        values = {name.rsplit(".", 1)[1]: item.value for name, item in snapshot.fields.items()}
        selected = [values["provider"]] + ([values["fallback"]] if values["fallback"] else [])
        if (any(item not in app.router.providers for item in selected)
                or any(values["max_output_tokens"] > app.router.providers[item].maximum_output_tokens for item in selected)):
            raise TaskError("staging_model_configuration_mismatch", "current model selection exceeds the pinned staging profile")
        return snapshot

    def require_delivery_authorization(self):
        """Compatibility hook; live V2 delivery is governed by target and person access."""
        if self.review_only:
            raise TaskError("staging_review_only", "this internal rehearsal session only records local drafts")
        return None

    def require_intake_authorization(self):
        if not self.review_only:
            self.require_delivery_authorization()

    def _delivery_authorization_report(self):
        return {"required": False, "authorized": True, "state": "not_required",
                "grant_id": None, "capability": None, "expires_at": None}

    def _pin(self):
        operations = OperationRepository(self.runtime)
        with self.runtime.transaction():
            if self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE origin_key != ? LIMIT 1", (
                    self.profile.target.key,)) or self.runtime.read_one(
                    "SELECT 1 FROM conversation_batches WHERE origin_key != ? LIMIT 1", (self.profile.target.key,)):
                raise TaskError("staging_store_in_use", "staging cannot adopt another target's conversation runtime")
            previous = operations.get(self.PIN_ID)
            if previous.is_success:
                receipt = previous.unwrap()
                pin_matches = receipt.parameter_hash in (self.profile_hash, self._legacy_profile_hash)
                if (receipt.operation.owner != "host.staging" or receipt.operation.scope != self.profile.target.scope
                        or (not pin_matches and not self.bootstrap_receipt_validated)
                        or receipt.state is not OperationState.COMPLETED):
                    raise TaskError("staging_profile_conflict", "this store belongs to another fixed staging profile")
                return
            if previous.failure.code != "operation_not_found":
                require(previous)
            if self.runtime.read_one("SELECT 1 FROM execution_tasks LIMIT 1", ()) or self.runtime.read_one(
                    "SELECT 1 FROM conversation_batches LIMIT 1", ()):
                raise TaskError("staging_store_in_use", "staging cannot adopt an existing conversation runtime")
            operation = OperationRef(self.PIN_ID, "host.staging", self.profile.target.scope,
                VersionRef(self.PIN_ID, 1), self.PIN_ID)
            require(operations.reserve(operation, parameter_hash=self.profile_hash, observed_at=self.application.now()))
            require(operations.transition(self.PIN_ID, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=self.application.now()))

    async def start(self, *, expected_task_generation: int, expected_model_generation: int):
        async with self._lifecycle:
            if self._starting or self.application.state != "new":
                return Outcome.failed("host_already_started", "staging has a single startup lifecycle")
            self._starting = True
        try:
            # Refuse sender startup before any native preflight when the
            # independent external-effect grant is absent or stale.
            self.require_intake_authorization()
            self._configuration()
            # A slow host lookup must not prevent close() from shutting intake.
            instruction = await self.bridge.verify_role(self.profile.target.scope, self.profile.target.role_binding)
            for model in self.models:
                model._current()
            async with self._lifecycle:
                if self.application.state != "new":
                    return Outcome.failed("host_not_running", "staging was closed during native preflight")
                # Host awaits can overlap permission edits. Recheck before durable admission.
                self.require_intake_authorization()
                self._configuration()
                self.application.life_runtime.bind(self.profile.target, instruction, guard=self.bridge.verify_role)
                self.application.state_maintenance.bind(self.profile.target, instruction, guard=self.bridge.verify_role)
                self.application.contact_schedule.bind_context(self.profile.target)
                if self.application.presence is not None:
                    self.application.presence.bind(self.profile.target)
                self._pin()
                if callable(getattr(self.native_tool_port, "register_catalog", None)):
                    require(self.native_tool_port.register_catalog(observed_at=self.application.now()))
                result = await self.application.start(expected_task_generation=expected_task_generation,
                                                       expected_model_generation=expected_model_generation)
                if result.is_success and callable(getattr(self.native_tool_port, "begin_start", None)):
                    self.native_tool_port.begin_start()
                return result
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except asyncio.CancelledError:
            raise
        except Exception:
            return Outcome.failed("staging_preflight_failed", "native staging preflight failed; intake remains closed")
        finally:
            self._starting = False

    async def receive(self, host_event):
        # This session owns one private target. Keep a slow attachment capture
        # from allowing the next message to overtake its original input.
        async with self._intake:
            return await self._receive(host_event)

    async def plan_calendar(self, host_event, *, mode, refresh=False):
        async with self._intake:
            self.application.require_running()
            self.require_intake_authorization()
            self._configuration()
            captured = require(await self.bridge.capture(host_event))
            target = self.profile.target
            if (captured.principal != target.actor.principal or captured.route.scope != target.scope
                    or captured.recipients != target.audience or captured.role != target.role_binding.role):
                raise TaskError("staging_target_mismatch", "event differs from the explicitly selected staging target")
            binding = self.application.state_maintenance.bindings.get(target.key)
            if binding is None:
                raise TaskError("planning_role_changed", "original role binding is unavailable")
            return await self.application.calendar_planning.run(target, binding[1], mode=mode,
                slot="manual:" + mode + ":" + captured.event_id, manual=True, refresh=refresh)

    async def _receive(self, host_event):
        try:
            self.application.require_running()
            self.require_intake_authorization()
            self._configuration()
            captured = require(await self.bridge.capture(host_event))
            target = self.profile.target
            if (captured.principal != target.actor.principal or captured.route.scope != target.scope
                    or captured.recipients != target.audience or captured.role != target.role_binding.role):
                raise TaskError("staging_target_mismatch", "event differs from the explicitly selected staging target")
            self.application.require_running()
            self.require_intake_authorization()
            self._configuration()
            return self.host.receive(captured)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))

    async def _preview(self, envelope):
        # A persisted draft is useful rehearsal evidence, but is never sent history.
        if envelope.context.key != self.profile.target.key or envelope.before_dispatch is None:
            raise TaskError("staging_target_mismatch", "preview must retain its original owner")
        envelope.before_dispatch()
        return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key,
            envelope.payload_hash, self.sender_binding, "not_sent", "staging-preview:" + envelope.part_id)

    @outcome
    def inspect(self, batch_id=None):
        """Authorized summary, without message bodies, prompts, keys or endpoint URLs."""
        with self.runtime.read_snapshot():
            return self._inspect(batch_id)

    def _inspect(self, batch_id):
        app, target = self.application, self.profile.target
        snapshot = self._configuration()
        adoption = require(app.configuration.application_status(target.actor, target=self.config_target,
            group_id=self.model_profile.group, observed_at=app.now()))
        streaming_available = self.model.streaming_supported
        report = {"state": app.state, "profile_hash": self.profile_hash,
            "delivery_mode": "onebot_private", "configuration_token": snapshot.resolution_token,
            "review_only": self.review_only,
            "configuration_application": adoption.state, "model_generation": adoption.generation,
            "task_generation": app.tasks.generation, "model_transport": self.model.local_provider().transport,
            "streaming": {"requested": self.profile.streaming, "available": streaming_available,
                          "ready": not self.profile.streaming or streaming_available,
                          "transport": "astrbot_native_query_stream" if streaming_available else None,
                          "reason": None if not self.profile.streaming or streaming_available
                          else "streaming_transport_unavailable"},
            "model_config_hash": self.model.config_hash, "confirmed_delivery": False,
            "delivery_authorization": self._delivery_authorization_report()}
        if batch_id is not None:
            message = require(app.messages.observe(target, batch_id))
            basis = "staging_preview" if self._uses_local_sender else "onebot_reverse_websocket"
            message = {**message, "receipt_basis": basis,
                "sender": basis if any(p["task_id"] for p in message["parts"]) else "not_connected"}
            report["conversation"] = message
            report["preview_ready"] = False
            if self._uses_local_sender:
                try:
                    self._readable_draft(batch_id)
                    report["preview_ready"] = True
                except TaskError as error:
                    report["preview_unavailable_reason"] = error.code
            report["confirmed_delivery"] = message["confirmed_parts"] > 0
            report["delivery_receipts"] = [dict(row) for row in self.runtime.read_all(
                """SELECT part_id, operation_id, state, platform_ref, delivered_at
                   FROM conversation_parts WHERE batch_id = ? ORDER BY sequence""", (batch_id,))]
            if message["generation_task_id"]:
                report["model"] = require(app.gateway.observe(target, message["generation_task_id"]))
        return report

    def _readable_draft(self, batch_id):
        messages, target = self.application.messages, self.profile.target
        with self.application.tasks.read_only_context():
            batch = messages.owned(target, batch_id)
            if batch["state"] != "ready":
                raise TaskError("staging_draft_unavailable", "the original draft is not ready or has been invalidated")
            return messages._request(batch, target, self.application.now())

    @outcome
    def preview(self, batch_id):
        """Read the original generated draft after current source/owner checks."""
        with self.application.tasks.read_only_context():
            self._configuration()
            if not self._uses_local_sender:
                raise TaskError("staging_preview_disabled", "this profile uses the delivery receipt view")
            request = self._readable_draft(batch_id)
            rows = self.runtime.read_all("SELECT part_id, sequence, text FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch_id,))
            parts = self.application.messages.reply_images.project_parts(rows, self.application.messages.batch(batch_id),
                self.profile.target, self.application.now(), request=request)
            return {"sent": False, "parts": parts}

    async def close(self, *, grace_seconds=0.1):
        async with self._lifecycle:
            if callable(getattr(self.native_tool_port, "aclose", None)):
                await self.native_tool_port.aclose()
            elif callable(getattr(self.native_tool_port, "close", None)):
                self.native_tool_port.close()
            # The owner must keep the borrowed store open while late handles remain.
            return await self.application.close(grace_seconds=grace_seconds)
