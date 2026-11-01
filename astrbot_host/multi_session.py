"""One application serving private owners and independently authorized groups."""

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging

from companion_v2.adapters.astrbot.application import AstrBotCompanionHost
from companion_v2.adapters.astrbot.memory_owner import memory_owner_provider
from companion_v2.adapters.astrbot.memory_push import memory_push_provider
from companion_v2.adapters.astrbot.native import (
    NativeConversationInput, NativePrivateBridge, bounded_native_lookup, event_account_id,
    event_peer_id, native_client_connected, native_id, native_user_subject_id,
    supported_platform_name,
)
from companion_v2.adapters.astrbot.group import NativeGroupBridge
from companion_v2.adapters.astrbot.onebot import OneBotPrivateSender
from companion_v2.adapters.astrbot.onebot_group import OneBotGroupSender
from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
from companion_v2.adapters.astrbot.presence import OneBotPresencePort
from companion_v2.adapters.astrbot.provider import AstrBotTextProvider, REPLY_TASK_CAPABILITIES
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers, loaded_routing
from companion_v2.adapters.astrbot.tools import CompositeAstrBotToolPort
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.content.authoring_tasks import authoring_profiles
from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.conversation.messages import REPLY_KIND
from companion_v2.conversation.pokes import poke_reply_profile
from companion_v2.conversation.participation import group_participation_settings
from companion_v2.conversation.reply_settings import (
    GROUP, expression_prompt_sections, private_debounce_settings,
)
from companion_v2.conversation.private_debounce import (
    DEFAULTS as PRIVATE_DEBOUNCE_DEFAULTS, Policy as PrivateDebouncePolicy,
)
from companion_v2.conversation.media.review_service import image_review_profile
from companion_v2.conversation.media.context_caption import context_caption_profile
from companion_v2.conversation.media.image_narration import image_narration_profile
from companion_v2.conversation.media.video.narration import video_narration_profile
from companion_v2.conversation.media.understanding import intake_settings as file_intake_settings
from companion_v2.screen.interpretation import screen_interpretation_profile
from .settings import HostControlError
from .huawei_health.service import get_huawei_health_service_for_root
from companion_v2.conversation.voice_reply import VoiceReplyDelivery
from companion_v2.execution.models import Lane, ProviderBinding, TaskContext, TaskError, digest, require, stamp
from companion_v2.foundation.outcome import Failure, Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS, ModelTaskProfile
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.memory.reflection import reflection_profile
from companion_v2.proactive.service import contact_profile, persona_judge_profile
from companion_v2.role_work.journal.writing import writing_profiles
from companion_v2.role_work.reader import reading_profile
from companion_v2.role_work.state.maintenance import state_profile
from companion_v2.agenda.planning import planning_profile
from .weather_settings import configured_weather_ports
from companion_v2.role_work.growth.progress import growth_progress_profile
from companion_v2.role_work.journal.self_review_service import self_review_profile
from companion_v2.expression.style_service import expression_learning_profile
from companion_v2.expression.review import expression_review_profile
from companion_v2.conversation.relay_settings import configuration_fields as relay_configuration_fields
from .multi_authority import HOST_ID
from .model_settings import conversation_provider_resolver
from .group_settings import GroupTarget
from .group_admission import group_trigger_facts, mentioned_account_ids
from .voice import NativeVoiceReply
from .relay_tools import RelayToolPort


logger = logging.getLogger(__name__)


def persona_conversation(conversation_id, role):
    # Native conversation IDs alone would let raw sources from persona A appear
    # to belong to B after a switch. Preserve an immutable scope for each role.
    return conversation_id + ":v2-role:" + digest(role.subject_id)


def _confirm_current_persona_projection(bridge, account, selected, projection, instruction):
    fingerprint = digest(instruction)
    if fingerprint != projection.instruction_hash:
        # A prompt edit does not change the mapped character. Keep current
        # metadata so each live turn can compare against its captured text.
        projection = replace(projection, instruction_hash=fingerprint)
        if ((account.bot_id, selected) in bridge.personas
                or (account.bot_id, "[%None]") not in bridge.personas):
            bridge.personas[(account.bot_id, selected)] = projection
    return projection


async def _current_native_role(bridge, account, umo):
    """The selected persona ID fixes identity; its current text stays editable."""
    cid, selected, _, projection, instruction = await bridge._selected_role_snapshot(account, umo)
    projection = bridge._confirm_persona_projection(account, selected, projection, instruction)
    return bridge._conversation_key(umo, cid), projection.role, instruction


class MultiPrivateBridge(NativePrivateBridge):
    def __init__(self, *args, settings, authority, group_quote_loader=None, group_forward_loader=None,
                 group_component_reader=None, **kwargs):
        self.settings, self.authority = settings, authority
        self.targets = {target.route_key: target for target in settings.targets}
        super().__init__(*args, **kwargs)
        group_options = {**kwargs, "quote_loader": group_quote_loader, "component_reader": group_component_reader,
                         "reply_type": kwargs.get("reply_type") if group_quote_loader is not None else None,
                         "forward_loader": group_forward_loader,
                         "forward_type": kwargs.get("forward_type") if group_forward_loader is not None else None}
        self.groups = MultiGroupBridge(*args, group_targets=settings.group_targets, authority=authority,
                                       settings=settings, **group_options)

    def target_for_scope(self, scope):
        if scope.kind is ScopeKind.GROUP:
            return self.groups.target_for_scope(scope)
        target = self.targets.get((scope.platform, scope.account_id, scope.native_scope_id))
        if target is None and scope.kind is ScopeKind.PRIVATE:
            target = self.settings.private_target(scope.platform, scope.account_id, scope.native_scope_id)
        if scope.kind is not ScopeKind.PRIVATE or target is None or target.bot_id != scope.bot_id:
            raise TaskError("host_target_unavailable", "the native private target is not configured")
        return target

    def client(self, scope):
        if scope.kind is ScopeKind.GROUP:
            return self.groups.client(scope)
        self.target_for_scope(scope)
        return super().client(scope)

    async def capture_core_reply(self, event):
        """Authenticate a private route for AstrBot's reply path without reading attachments."""
        try:
            if not isinstance(event, self.event_type):
                raise TaskError("host_event_unsupported", "core reply requires a verified native event")
            message, session, meta = event.message_obj, event.session, event.platform_meta
            platform_id = meta.id
            if (getattr(getattr(message, "type", None), "value", None) != "FriendMessage"
                    or getattr(getattr(session, "message_type", None), "value", None) != "FriendMessage"
                    or getattr(message, "group_id", None)):
                raise TaskError("host_scene_unsupported", "core private reply requires a native private route")
            sender, login = event_peer_id(event), event_account_id(event)
            account = self.accounts.get((platform_id, login))
            target = self.settings.private_target(platform_id, login, sender)
            if (not supported_platform_name(meta.name) or session.platform_id != platform_id
                    or session.platform_name != platform_id or session.session_id != sender
                    or event.unified_msg_origin != self._umo(platform_id, sender)
                    or account is None or target is None or target.bot_id != account.bot_id):
                raise TaskError("host_route_mismatch", "event fields do not identify one registered private route")
            event_client = event.bot
            platform = self.context.get_platform_inst(platform_id)
            if (platform is None or platform.get_client() is not event_client
                    or not native_client_connected(platform, login, event_client)):
                raise TaskError("host_client_mismatch", "private event did not originate from the connected account client")

            event_id = native_id(message.message_id)
            at = datetime.fromtimestamp(event.created_at, timezone.utc)
            principal = AuthenticatedHostPrincipal(self.installation_id, self.host_id, platform_id,
                                                   sender, account.bot_id, login)
            self.authority.ensure_actor(target)
            actor = require(self.identities.resolve_authenticated(principal, observed_at=at))
            conversation_id, role, instruction = await self._role(account, sender)
            scope = ScopeRef(self.installation_id, platform_id, ScopeKind.PRIVATE, sender,
                             account.bot_id, login, conversation_id, sender)
            if event_client is not self.client(scope):
                raise TaskError("host_client_mismatch", "private host account changed during persona lookup")
            recipients = AudienceRef(scope, (actor.subject,))
            text = "".join(part.text for part in getattr(message, "message", ())
                           if getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) == "Plain"
                           and isinstance(getattr(part, "text", None), str))
            return Outcome.success(NativeConversationInput(
                event_id, principal, ConversationRoute(scope, user=actor.subject), recipients,
                text, at, role, instruction,
            ))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("host_core_reply_capture_failed", "private route or persona could not be verified")

    async def capture(self, event):
        if getattr(getattr(event.message_obj, "type", None), "value", None) == "GroupMessage":
            return await self.groups.capture(event, allow_text_fallback=True)
        message, session, meta = event.message_obj, event.session, event.platform_meta
        if (not isinstance(event, self.event_type)
                or getattr(getattr(message, "type", None), "value", None) != "FriendMessage"
                or getattr(getattr(session, "message_type", None), "value", None) != "FriendMessage"
                or getattr(message, "group_id", None)):
            return await super().capture(event)
        try:
            sender, login = event_peer_id(event), event_account_id(event)
            if (not supported_platform_name(meta.name) or session.platform_id != meta.id
                    or session.platform_name != meta.id or session.session_id != sender
                    or event.unified_msg_origin != f"{meta.id}:FriendMessage:{sender}"):
                return await super().capture(event)
            target = self.settings.private_target(meta.id, login, sender)
            if target is None:
                return await super().capture(event)
            account = self.accounts.get((meta.id, login))
            platform = self.context.get_platform_inst(meta.id)
            if (account is None or account.bot_id != target.bot_id or platform is None
                    or platform.get_client() is not event.bot
                    or not native_client_connected(platform, login, event.bot)):
                raise TaskError("host_client_mismatch", "private event did not originate from its registered Bot account")
            self.authority.ensure_actor(target)
        except (ValueError, AttributeError, TypeError):
            return await super().capture(event)
        result = await super().capture(event)
        if not result.is_success:
            return result
        try:
            return Outcome.success(await self._apply_window_link(target, result.unwrap()))
        except (TaskError, HostControlError, ValueError, KeyError) as error:
            code = getattr(error, "code", "host_window_link_failed")
            return Outcome.failed(code, str(error))

    async def _apply_window_link(self, target, captured):
        """Apply one explicit user-window merge after the normal native capture."""
        link = self.settings.window_link_for_target(target.target_id)
        if link is None:
            return captured
        primary = self.settings.target(link.primary_target_id)
        if primary.participant_id == "*":
            raise TaskError("host_window_link_invalid", "the primary private window is not concrete")
        account = self.accounts.get((primary.platform_id, primary.account_id))
        if account is None or account.bot_id != primary.bot_id:
            raise TaskError("host_window_link_invalid", "the primary Bot account is not connected")
        cid, role, instruction = await self._role(account, primary.participant_id)
        if link.mode == "role":
            # Keep the source window and its identity; only the memory owner and
            # character instruction come from the primary role.
            return replace(captured, role=role, instruction=instruction)

        self.authority.ensure_actor(primary)
        actor = self.authority.actor(primary)
        scope = ScopeRef(self.installation_id, primary.platform_id, ScopeKind.PRIVATE,
                         primary.participant_id, primary.bot_id, primary.account_id,
                         cid, primary.participant_id)
        principal = AuthenticatedHostPrincipal(self.installation_id, self.host_id, primary.platform_id,
                                               primary.participant_id, primary.bot_id, primary.account_id)
        return replace(captured, principal=principal,
                       route=ConversationRoute(scope, user=actor.subject),
                       recipients=AudienceRef(scope, (actor.subject,)),
                       role=role, instruction=instruction)

    async def capture_text_only(self, event):
        """Keep authenticated group text when optional native media is unavailable."""
        if getattr(getattr(event.message_obj, "type", None), "value", None) != "GroupMessage":
            return Outcome.failed("host_scene_unsupported", "text-only fallback requires a native group event")
        return await self.groups.capture_text_only(event)

    @staticmethod
    def _role_conversation_id(conversation_id, role):
        return persona_conversation(conversation_id, role)

    async def _role_for_umo(self, account, umo):
        return await _current_native_role(self, account, umo)

    def _confirm_persona_projection(self, account, selected, projection, instruction):
        return _confirm_current_persona_projection(self, account, selected, projection, instruction)

    async def verify_role(self, scope, binding, instruction=None):
        if scope.kind is ScopeKind.GROUP:
            return await self.groups.verify_role(scope, binding, instruction)
        result = await super().verify_role(scope, binding, instruction)
        current = require(self.authority.personas.resolve(scope, observed_at=self.now())).binding
        if current != binding:
            raise TaskError("role_binding_changed", "the native role context was replaced")
        return result

    async def verified_persona(self, scope, binding):
        if scope.kind is ScopeKind.GROUP:
            return await self.groups.verified_persona(scope, binding)
        persona = await super().verified_persona(scope, binding)
        current = require(self.authority.personas.resolve(scope, observed_at=self.now())).binding
        if current != binding:
            raise TaskError("role_binding_changed", "the native role context was replaced")
        return persona


class MultiGroupBridge(NativeGroupBridge):
    def __init__(self, *args, authority, settings, **kwargs):
        self.authority, self.settings = authority, settings
        super().__init__(*args, **kwargs)

    def target_for_route(self, platform_id, account_id, group_id):
        return (super().target_for_route(platform_id, account_id, group_id)
                or self.settings.group_target(platform_id, account_id, group_id))

    @staticmethod
    def _role_conversation_id(conversation_id, role):
        return persona_conversation(conversation_id, role)

    async def _role_for_umo(self, account, umo):
        return await _current_native_role(self, account, umo)

    async def verify_role(self, scope, binding, instruction=None):
        result = await super().verify_role(scope, binding, instruction)
        if require(self.authority.personas.resolve(scope, observed_at=self.now())).binding != binding:
            raise TaskError("role_binding_changed", "the native group role context was replaced")
        return result

    def _confirm_persona_projection(self, account, selected, projection, instruction):
        return _confirm_current_persona_projection(self, account, selected, projection, instruction)

    async def verified_persona(self, scope, binding):
        persona = await super().verified_persona(scope, binding)
        if require(self.authority.personas.resolve(scope, observed_at=self.now())).binding != binding:
            raise TaskError("role_binding_changed", "the native group role context was replaced")
        return persona


class MultiPresence:
    def __init__(self, session):
        self.session = session

    def port(self, context):
        session = self.session
        return OneBotPresencePort(session.bridge, reverse_api_type=session.types.reverse_api,
            authorize=lambda: session.authorize(context), now=session.now)

    async def typing(self, context, **kwargs):
        return await self.port(context).typing(context, **kwargs)

    async def custom_status(self, context, wording, **kwargs):
        return await self.port(context).custom_status(context, wording, **kwargs)

    async def online_status(self, context, mode, **kwargs):
        return await self.port(context).online_status(context, mode, **kwargs)


class ReviewFallbackRoutes:
    """Resolve a group member's own configured private route, or nothing.

    A group member is never given a private conversation implicitly. The route
    exists only when the same account already runs one of the installation's
    configured private targets for that member, that target still has current
    delivery authorization, and the group itself is an explicit group target.
    Anything else keeps the reviewed image out of every channel.
    """

    def __init__(self, session):
        self.session = session

    def __call__(self, group):
        session = self.session
        authority, settings = session.authority, session.settings
        try:
            group_target = session.bridge.target_for_scope(group.scope)
            if not isinstance(group_target, GroupTarget):
                return None
            member = group.actor.principal.native_account_id
            for target in settings.targets:
                if (target.platform_id, target.account_id, target.bot_id, target.participant_id) != (
                        group.scope.platform, group.scope.account_id, group.scope.bot_id, member):
                    continue
                # 正式版没有"投递授权"这道闸：解析到目标即可用（见 multi_authority.delivery_access）。
                try:
                    authority.delivery_access(target)
                except TaskError:
                    return None
                context = authority.context(target.target_id, group.role_binding.role.subject_id)
                return context
            return None
        except TaskError:
            return None


@dataclass(frozen=True)
class CoreReplyPlan:
    """Resolved identity plus the reply settings needed before model preparation."""

    captured: NativeConversationInput
    context: TaskContext
    debounce: PrivateDebouncePolicy | None = None


class PrivateDialogueDenied(TaskError):
    """A private refusal bound to the exact context that was denied."""

    def __init__(self, context):
        super().__init__("private_access_dialogue_denied",
                         "this private user has no dialogue permission")
        self.refusal = context


@dataclass(frozen=True)
class CoreReplyOutcome(Outcome):
    """Outcome that can carry the refusal belonging to this call only.

    The refused context never lives on the shared session: concurrent events
    for different Bot accounts must each keep their own copy, cooldown and
    route.  Success values remain the same ``CoreReplyPlan`` or prepared
    mapping as before.
    """

    refusal: TaskContext | None = None

    @classmethod
    def from_failure(cls, error):
        return cls(failure=Failure(error.code, str(error)),
                   refusal=getattr(error, "refusal", None))


class MultiHostSession:
    def __init__(self, runtime, native_context, *, settings, personas, authority, native_types,
                 provider_fingerprint, runtime_instance_id, now, native_tool_port=None, news_port=None,
                 solar_term_port=None, qzone_transport=None, balance_port=None, weather_ports=None,
                 persona_window_snapshot_lookup=None, persona_window_snapshot_update=None):
        self.runtime, self.settings, self.authority, self.types, self.now = runtime, settings, authority, native_types, now
        self.native_tool_port = native_tool_port
        self.huawei_health = get_huawei_health_service_for_root(runtime.data_root, logger=logger)
        common = settings.common
        weather_port, weather_alert_port = weather_ports or configured_weather_ports(None)
        self.voice = NativeVoiceReply(native_context).render
        self.voice_delivery = VoiceReplyDelivery(runtime, now)
        self.bridge = MultiPrivateBridge(native_context, settings=settings, authority=authority,
            version=native_types.version, event_type=native_types.event, installation_id=runtime.installation_id,
            host_id=HOST_ID, identities=authority.identities, accounts=settings.accounts, personas=personas,
            image_type=native_types.image, image_loader=native_types.image_loader,
            reply_type=native_types.reply, quote_loader=native_types.quote_loader,
            group_quote_loader=native_types.group_quote_loader,
            group_forward_loader=native_types.group_forward_loader,
            group_component_reader=native_types.group_component_reader,
            forward_type=native_types.forward, forward_loader=native_types.forward_loader, now=now,
            forward_intake=common.forward_intake,
            audio_type=native_types.audio, audio_loader=native_types.audio_loader, component_reader=native_types.component_reader,
            video_type=native_types.video, video_loader=native_types.video_loader,
            file_type=native_types.file, file_loader=native_types.file_loader,
            session_persona_override_lookup=native_types.session_persona_override_lookup,
            persona_snapshot_lookup=native_types.persona_snapshot_lookup,
            persona_selection_lookup=native_types.persona_selection_lookup,
            persona_window_snapshot_lookup=persona_window_snapshot_lookup,
            persona_window_snapshot_update=persona_window_snapshot_update,
            file_settings=lambda scope, role: file_intake_settings(runtime, scope, role))
        self.model = AstrBotTextProvider(native_context, bridge=self.bridge,
            binding=ProviderBinding("model.text", common.provider_id, native_types.version, provider_fingerprint),
            provider_id=common.provider_id, version=native_types.version, provider_type=native_types.provider_types,
            function_tool_type=native_types.function_tool, tool_set_type=native_types.tool_set,
            # Provider ceiling, not the default budget: the budgets below travel with
            # each task profile, so sharing one number caps every task at the default.
            tariff=common.tariff, maximum_output_tokens=MAX_MODEL_OUTPUT_TOKENS,
            maximum_input_estimate=common.maximum_input_estimate, output_parameter=common.output_parameter,
            streaming=common.streaming)
        self.model._current()
        self.models = (self.model,) + assemble_providers(native_context, bridge=self.bridge,
            native_types=native_types, primary_id=common.provider_id, additional=common.additional_providers,
            defaults=TextProviderSettings(common.provider_id, common.tariff, MAX_MODEL_OUTPUT_TOKENS,
                common.maximum_input_estimate, common.output_parameter, common.streaming))
        fallback_provider_id, replacement_rules = loaded_routing(common.fallback_provider_id,
            common.replacement_rules, (model.binding.provider for model in self.models))
        profiles = (ModelTaskProfile(REPLY_KIND, Lane.FOREGROUND, common.provider_id, common.task_prompt,
                    default_fallback=fallback_provider_id, replacement_rules=replacement_rules,
                    fallback_on_refusal=common.fallback_on_refusal,
                    max_output_tokens=common.max_output_tokens, capabilities=REPLY_TASK_CAPABILITIES,
                    prompt_sections=expression_prompt_sections()),
                    poke_reply_profile(common.provider_id, max_output_tokens=min(1024, common.max_output_tokens)),
                    contact_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    persona_judge_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    reflection_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    state_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    planning_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    growth_progress_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    self_review_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    expression_learning_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    expression_review_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    expression_review_profile(common.provider_id, max_output_tokens=common.max_output_tokens, contact=True),
                    reading_profile(common.provider_id, max_output_tokens=common.max_output_tokens),
                    image_review_profile(common.provider_id),
                    context_caption_profile(common.provider_id),
                    screen_interpretation_profile(common.provider_id),
                    *authoring_profiles(common.provider_id, draft_tokens=common.max_output_tokens),
                    *writing_profiles(common.provider_id, max_output_tokens=common.max_output_tokens),
                    *((image_narration_profile(next(model.binding.provider for model in self.models
                        if "image" in model.local_provider().capabilities)),)
                      if any("image" in model.local_provider().capabilities for model in self.models) else ()),
                    *((video_narration_profile(next(model.binding.provider for model in self.models
                        if "video" in model.local_provider().capabilities)),)
                      if any("video" in model.local_provider().capabilities for model in self.models) else ()))
        modes = ["onebot_private", "onebot_group"]
        self.sender_binding = ProviderBinding("message.delivery", "astrbot-multi." + ".".join(modes),
            native_types.version, digest([runtime.installation_id, *modes]))
        relay_port = RelayToolPort(self.bridge, native_types=native_types)
        tool_port = CompositeAstrBotToolPort(native_tool_port, relay_port)
        registry_fields = configuration_fields(profiles)
        registry_ids = {item.field_id for item in registry_fields}
        registry_fields += tuple(field for field in relay_configuration_fields()
                                 if field.field_id not in registry_ids)
        self.host = AstrBotCompanionHost(runtime, event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, runtime_identity=RuntimeIdentity("companion", runtime_instance_id, "astrbot", HOST_ID),
            registry=FieldRegistry(version="astrbot-multi.v1", fields=registry_fields),
            profiles=profiles, providers=tuple(model.local_provider() for model in self.models), budget=common.budget,
            budget_soft=common.budget_soft,
            default_provider_resolver=conversation_provider_resolver(native_context),
            sender=LocalSender(self.sender_binding, self.send, supports_images=True, supports_poke=True), policy=common.policy, limits=common.limits, now=now,
            tool_port=tool_port, segmentation=common.segmentation,
            # 记忆归属读的是别家插件自己公开的声明；跨插件读取只在这一个适配函数里，
            # 核心不反向导入宿主包。写入端口复用同一条发现路径：日记、梦境与主动
            # 消息完成后推给当前对外表达记忆的一方，失败不影响已完成的领域动作。
            memory_owner=memory_owner_provider(native_context),
            memory_push=memory_push_provider(native_context),
            weather_port=weather_port,
            weather_alert_port=weather_alert_port,
            balance_port=balance_port,
            news_port=news_port,
            solar_term_port=solar_term_port,
            qzone_transport=qzone_transport,
            voice_policy=common.voice_reply_policy if common.voice_reply_enabled else None,
            wake_port=self._wake_snapshot,
            presence_port=MultiPresence(self))
        self.application = self.host.application
        if hasattr(self.application, "contact_schedule"):
            self.application.contact_schedule.bind_health_source(self.huawei_health)
        self.application.image_fallback.attach_port(ReviewFallbackRoutes(self))
        # 私聊权限的**默认值**要在签发那一刻就读到：新用户一开始有没有对话/主动权限，
        # 由 `private_access` 那一组决定（见 companion_v2/relationships/private_access.py）。
        self.authority.attach_private_access(self.application.private_access_settings)
        if callable(getattr(native_tool_port, "bind_role_guard", None)):
            native_tool_port.bind_role_guard(self.bridge.verify_role, self.bridge.verified_persona)
        self._intake = defaultdict(asyncio.Lock, {target.target_id: asyncio.Lock() for target in settings.all_targets})
        from .core_reply_maintenance import CoreReplyMaintenance
        self.core_reply_maintenance = CoreReplyMaintenance(self)
        self.core_reply_observations = None
        self._native_turns_recovered = False
        try:
            from .core_reply_observations import CoreReplyObservations
            self.core_reply_observations = CoreReplyObservations(self)
        except Exception as error:
            logger.warning("[CompanionV2] 原生聊天观察初始化降级 error_type=%s", type(error).__name__)

    def scope(self, target, conversation_id):
        return ScopeRef(self.runtime.installation_id, target.platform_id, ScopeKind.PRIVATE, target.participant_id,
                        target.bot_id, target.account_id, conversation_id, target.participant_id)

    def _wake_snapshot(self, context):
        """Read the Reality wake signals for this exact private target.

        The port is deliberately narrow: it returns the gateway's typed mobile
        projection and the wake session state, never message bodies, feedback
        text or device identifiers.
        """
        registry = self.native_tool_port
        getter = getattr(registry, "api", None)
        api = getter("reality") if callable(getter) else None
        if api is None:
            return {}
        principal = getattr(getattr(context, "actor", None), "principal", None)
        user_id = getattr(principal, "native_account_id", "")
        if not isinstance(user_id, str) or not user_id.strip():
            return {}
        mobile_getter = getattr(api, "mobile_context", None)
        wakeup_getter = getattr(api, "wakeup_snapshot", None)
        mobile = mobile_getter(user_id.strip()) if callable(mobile_getter) else {}
        wakeup = wakeup_getter(user_id.strip()) if callable(wakeup_getter) else {}
        return {"mobile": mobile if isinstance(mobile, dict) else {},
                "wakeup": wakeup if isinstance(wakeup, dict) else {}}

    async def synchronize(self, target):
        account = self.bridge.accounts[(target.platform_id, target.account_id)]
        cid, role, instruction = await self.bridge._role(account, target.participant_id)
        scope = self.scope(target, cid)
        self.bridge.client(scope)
        context = self.authority.select(target, scope, role)
        if self.application.presence is not None:
            self.application.presence.bind(context)
        return context, instruction

    def _prepared_wildcard_private_targets(self):
        """Return real private windows already materialized from a wildcard.

        A wildcard is an admission rule, never a person.  During startup we
        may nevertheless have concrete, authenticated scopes from the probe.
        Reusing those stored scopes lets background routines bind immediately
        without inventing a participant or waiting for the next message.
        """
        prefix = "host.multi.context:"
        wildcard_accounts = {
            (target.platform_id, target.account_id, target.bot_id)
            for target in self.settings.targets if target.participant_id == "*"
        }
        if not wildcard_accounts or self.runtime is None:
            return ()
        found = {}
        for row in self.runtime.read_all(
                "SELECT key, value FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                (len(prefix), prefix)):
            try:
                scope = ScopeRef.restore(json.loads(row["value"]))
            except (TypeError, ValueError, KeyError):
                continue
            account = (scope.platform, scope.account_id, scope.bot_id)
            if (scope.kind is not ScopeKind.PRIVATE or scope.native_scope_id == "*"
                    or account not in wildcard_accounts):
                continue
            target = self.settings.private_target(scope.platform, scope.account_id, scope.native_scope_id)
            if target is None or target.participant_id == "*":
                continue
            template = self.settings.template_for(target)
            if template is None or template.participant_id != "*":
                continue
            resolution = self.authority.personas.resolve(scope, observed_at=self.now())
            if resolution.failure is not None:
                continue
            role_id = resolution.unwrap().binding.role.subject_id
            if not any(persona.bot_id == target.bot_id and persona.role_id == role_id
                       for persona in self.settings.personas):
                continue
            found[target.target_id] = target
        return tuple(found.values())

    async def start(self, *, expected_task_generation, expected_model_generation):
        if self.core_reply_observations is not None and not self._native_turns_recovered:
            from companion_v2.conversation.native_turns import NativeConversationTurns
            def interrupt_previous_runner():
                return NativeConversationTurns(self.runtime).interrupt_open(at=self.now())
            await asyncio.to_thread(interrupt_previous_runner)
            self._native_turns_recovered = True
        targets = [target for target in self.settings.primary_targets if target.participant_id != "*"]
        targets.extend(self._prepared_wildcard_private_targets())
        seen = set()
        for target in targets:
            if target.target_id in seen:
                continue
            seen.add(target.target_id)
            try:
                context, instruction = await self.synchronize(target)
            except TaskError as error:
                if error.code in ("host_persona_unmapped", "host_persona_window_unbound"):
                    # Leave this target unrouted until its V2 window persona is explicit.
                    continue
                raise
            self.application.life_runtime.bind(context, instruction, guard=self.bridge.verify_role)
            self.application.state_maintenance.bind(context, instruction, guard=self.bridge.verify_role)
            self.application.contact_schedule.bind_context(context)
            if self.application.presence is not None:
                self.application.presence.bind(context)
        for model in self.models:
            model._current()
        if self.application.state != "new":
            raise TaskError("host_not_running", "shared host was closed during native preflight")
        if callable(getattr(self.native_tool_port, "register_catalog", None)):
            require(self.native_tool_port.register_catalog(observed_at=self.now()))
        result = await self.application.start(expected_task_generation=expected_task_generation,
                                               expected_model_generation=expected_model_generation)
        if result.is_success and callable(getattr(self.native_tool_port, "begin_start", None)):
            self.native_tool_port.begin_start()
        return result

    async def receive(self, event, target, *, group_admission=None):
        async with self._intake[target.target_id]:
            captured = None
            try:
                self.application.require_running()
                captured = require(await self.bridge.capture(event))
                if self.bridge.target_for_scope(captured.route.scope) != target:
                    raise TaskError("host_target_mismatch", "input changed its configured native route")
                self.application.require_running()
                # Admission, active-role fencing and input publication are one
                # transaction. Rejected inputs cannot change another task's role.
                with self.runtime.transaction():
                    context = (self.authority.groups.select(target, captured) if captured.route.scope.kind is ScopeKind.GROUP
                               else self.authority.select(target, captured.route.scope, captured.role))
                    if context.scope.kind is ScopeKind.PRIVATE:
                        # 私聊：对话权限按人判定。没有权限时不进对话；回话由控制器
                        # 在事务之外发（那里才有平台客户端），这里把对象随本次失败带出。
                        if self.application.private_access_gate.denied(context, at=self.now()):
                            raise PrivateDialogueDenied(context)
                    else:
                        require(self.application.tasks.check_context_access(context, action="conversation.reply", observed_at=self.now()))
                    def admitted(original, batch_id, *, replayed):
                        if original.scope.kind is ScopeKind.GROUP:
                            self.authority.group_delivery.admit(target, original, batch_id,
                                self.sender_binding, replayed=replayed)
                            if group_admission is not None:
                                if (group_admission["context_key"] != original.key
                                        or group_admission["event_id"] != captured.event_id):
                                    raise TaskError("group_admission_changed", "group reply candidate changed during capture")
                                self.application.tools.group_participation.admission.record(
                                    original, batch_id, reason=group_admission["reason"],
                                    mentioned=group_admission["mentioned"])
                    result = require(self.host.receive(captured, on_admitted=admitted))
                self._record_platform_names(event, context)
                if context.scope.kind is ScopeKind.PRIVATE:
                    self.application.life_runtime.bind(context, captured.instruction, guard=self.bridge.verify_role)
                    self.application.state_maintenance.bind(context, captured.instruction, guard=self.bridge.verify_role)
                    self.application.contact_schedule.bind_context(context)
                    if self.application.presence is not None:
                        self.application.presence.bind(context)
                return Outcome.success(result)
            except TaskError as error:
                if isinstance(target, GroupTarget):
                    scope = getattr(captured, "route", None)
                    scope = getattr(scope, "scope", None)
                    logger.warning(
                        "[CompanionV2] 群入站拒绝 code=%s target=%s platform=%s account=%s group=%s session=%s conversation=%s role=%s",
                        error.code, target.target_id, target.platform_id, target.account_id,
                        target.group_id, getattr(getattr(event, "session", None), "session_id", ""),
                        getattr(scope, "conversation_id", ""),
                        getattr(getattr(captured, "role", None), "subject_id", ""),
                    )
                return CoreReplyOutcome.from_failure(error)
            except Exception as error:
                if isinstance(target, GroupTarget):
                    logger.error("[CompanionV2] 群入站异常 target=%s error_type=%s",
                                 target.target_id, type(error).__name__)
                    return Outcome.failed("host_group_receive_failed", "group input could not be admitted")
                raise

    async def _materialize_core_conversation(self, event, target, captured):
        """Fix the first native CID before V2 seals any optional turn background."""
        if not captured.route.scope.conversation_id.startswith("astrbot-conversation:unopened-"):
            return captured
        manager = self.bridge.context.conversation_manager
        umo = event.unified_msg_origin
        try:
            cid = await bounded_native_lookup(manager.get_curr_conversation_id(umo), operation="conversation_id")
            if not cid:
                create = getattr(manager, "new_conversation", None)
                if not callable(create):
                    raise TaskError("host_core_reply_conversation_unavailable",
                                    "AstrBot cannot create the first native conversation")
                cid = await bounded_native_lookup(create(umo, target.platform_id), operation="conversation_create")
            if not cid:
                raise TaskError("host_core_reply_conversation_unavailable",
                                "AstrBot did not return the first native conversation")
        except TaskError as error:
            raise TaskError("host_core_reply_conversation_unavailable",
                            "the first native conversation could not be prepared") from error
        except Exception as error:
            raise TaskError("host_core_reply_conversation_unavailable",
                            "the first native conversation could not be prepared") from error
        current = require(await (self.bridge.capture_text_only(event)
            if isinstance(target, GroupTarget) else self.bridge.capture_core_reply(event)))
        if (current.principal != captured.principal or current.role != captured.role
                or self.bridge.target_for_scope(current.route.scope) != target):
            raise TaskError("core_reply_context_changed", "identity or character changed while creating the native conversation")
        return current

    async def prepare_group(self, event, target, *, reply_candidate=False):
        """Authenticate a group route before the normal inbound pipeline.

        AstrBot resolves the selected persona again while the V2 inbound
        adapter publishes the source.  Group routes used to create their
        persona binding only inside ``receive``; if that second resolution
        happened before the savepoint was visible, the whole message was
        rejected as ``role_binding_missing``.  V1's early observer avoided
        this coupling.  Materialize the same authenticated group binding here
        and commit it before the reply path starts.  No source, model call or
        platform send happens in this preflight.
        """
        if not isinstance(target, GroupTarget):
            return Outcome.failed("host_scene_unsupported", "group preflight requires a configured group target")
        async with self._intake[target.target_id]:
            try:
                self.application.require_running()
                captured = require(await self.bridge.capture_text_only(event))
                if reply_candidate:
                    captured = await self._materialize_core_conversation(event, target, captured)
                if (captured.route.scope.kind is not ScopeKind.GROUP
                        or self.bridge.target_for_scope(captured.route.scope) != target):
                    raise TaskError("host_target_mismatch", "input changed its configured native group route")
                # Keep this transaction independent from the later source and
                # batch transaction.  The role binding must already be visible
                # when AstrBotInboundAdapter resolves the persona a second time.
                with self.runtime.transaction():
                    context = self.authority.groups.select(target, captured)
                # V1 records the bounded group observation before its reply
                # handlers run.  Do the same after the authenticated binding is
                # committed, so a later media/model failure cannot erase this
                # message from the next group turn's reference material.
                participation = getattr(getattr(self.application, "tools", None),
                                        "group_participation", None)
                observe_early = getattr(participation, "observe_early", None)
                if callable(observe_early):
                    observe_early(context, text=captured.text,
                                  speaker_id=captured.principal.native_account_id,
                                  received_at=captured.at, event_id=captured.event_id)
                self._record_platform_names(event, context)
                if reply_candidate:
                    from companion_v2.conversation.reply_settings import GROUP
                    snapshot = self.application.reply_settings.snapshot(context.actor, context, self.now())
                    settings = {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}
                    values = group_participation_settings(self.runtime, context)
                    projection = next((persona for persona in self.bridge.groups.personas.values()
                                       if persona.bot_id == context.scope.bot_id and persona.role == captured.role), None)
                    facts = group_trigger_facts(event, context.scope.account_id)
                    try:
                        continuation = participation.admission.continuation_status(context, captured.instruction,
                            captured.at, settings=settings, event_id=captured.event_id)
                    except Exception as error:
                        raise TaskError("group_continuation_unavailable",
                                        "automatic group follow-up policy could not be verified") from error
                    reason = participation.admission.candidate(context, text=captured.text,
                        instruction=captured.instruction, at=captured.at, settings=settings, participation=values,
                        persona_name=projection.persona_id if projection is not None else "", event_id=captured.event_id,
                        continuation=continuation, **facts)
                    from companion_v2.conversation import group_high_intensity
                    closeout = participation.high_intensity_state(
                        context, captured.instruction, at=captured.at, settings=settings)
                    if reason and not group_high_intensity.allows(reason, closeout):
                        reason = ""
                    if not reason and facts["has_image"] and self.core_reply_observations is not None:
                        try:
                            self.core_reply_observations.media(event, {
                                "context": context, "native_input": captured,
                                "event_id": captured.event_id,
                                "turn_id": "core-reply:" + digest([context.key, captured.event_id]),
                            }, collection_only=True)
                        except Exception as error:
                            logger.warning("[CompanionV2] 群表情后台收录调度降级 error_type=%s",
                                           type(error).__name__)
                    from companion_v2.conversation.reply_settings import group_wakeup_settings
                    wakeup = group_wakeup_settings(settings)
                    return Outcome.success({"admitted": bool(reason), "reason": reason,
                        "continuation": continuation,
                        "mentioned": facts["mentioned"], "context_key": context.key, "event_id": captured.event_id,
                        "participation_policy": {
                            "question_enabled": wakeup["group_wakeup_enabled"] and wakeup["group_wakeup_question_enabled"],
                            "question_threshold": wakeup["group_wakeup_question_threshold"],
                            "interjection_enabled": wakeup["group_wakeup_enabled"] and values["enable_group_interjection"],
                            "interest_probability": wakeup["group_wakeup_interest_probability"],
                            "cooldown_seconds": wakeup["group_wakeup_cooldown_seconds"],
                        },
                        "question_reference": participation.admission.question_reference(context,
                            text=captured.text, instruction=captured.instruction, at=captured.at,
                            settings=settings, event_id=captured.event_id) if reason == "question" else None})
                return Outcome.success(context)
            except TaskError as error:
                return Outcome.failed(error.code, str(error))
            except Exception as error:
                logger.error("[CompanionV2] 群消息早期预处理异常 target=%s error_type=%s",
                             target.target_id, type(error).__name__)
                return Outcome.failed("host_group_preflight_failed", "group route preflight failed")

    def _require_core_reply_target(self, target, captured):
        if self.bridge.target_for_scope(captured.route.scope) != target:
            raise TaskError("host_target_mismatch", "input changed its configured native route")

    def _select_core_reply_context(self, target, captured):
        with self.runtime.transaction():
            context = (self.authority.groups.select(target, captured)
                       if captured.route.scope.kind is ScopeKind.GROUP
                       else self.authority.select(target, captured.route.scope, captured.role))
            if context.scope.kind is ScopeKind.PRIVATE:
                if self.application.private_access_gate.denied(context, at=self.now()):
                    raise PrivateDialogueDenied(context)
            else:
                require(self.application.tasks.check_context_access(
                    context, action="conversation.reply", observed_at=self.now()))
        return context

    @staticmethod
    def _validate_core_group_admission(group_admission, context, captured):
        if group_admission is None:
            return
        if (not isinstance(group_admission, Mapping)
                or group_admission.get("admitted") is not True
                or group_admission.get("context_key") != context.key
                or group_admission.get("event_id") != captured.event_id):
            raise TaskError("group_admission_changed", "group reply candidate changed during capture")

    async def prepare_core_reply_context(self, event, target, *, group_admission=None):
        """Resolve identity and reply settings without starting preparation.

        The private debounce needs the window configuration before it can start
        the quiet timer.  This bounded first phase captures the native input,
        authorizes the context and reads the reply snapshot; the full preparation
        can then reuse the returned plan while the quiet timer runs.
        """
        try:
            self.application.require_running()
            captured = require(await (
                self.bridge.capture_text_only(event)
                if isinstance(target, GroupTarget)
                else self.bridge.capture_core_reply(event)
            ))
            captured = await self._materialize_core_conversation(event, target, captured)
            self._require_core_reply_target(target, captured)
            context = self._select_core_reply_context(target, captured)
            self._validate_core_group_admission(group_admission, context, captured)
            debounce = None
            try:
                snapshot = self.application.reply_settings.snapshot(
                    context.actor, context, self.now())
                values = {name: snapshot.values[GROUP + "." + name]
                          for name in PRIVATE_DEBOUNCE_DEFAULTS}
                debounce = private_debounce_settings(values)
            except Exception as error:
                logger.warning("[CompanionV2] 私聊防抖设置暂不可读，保留普通回复 error_type=%s",
                               type(error).__name__)
            return CoreReplyOutcome.success(CoreReplyPlan(captured, context, debounce))
        except TaskError as error:
            return CoreReplyOutcome.from_failure(error)
        except Exception as error:
            logger.error("[CompanionV2] 核心回复上下文解析失败 target=%s error_type=%s",
                         target.target_id, type(error).__name__)
            return Outcome.failed("host_core_reply_prepare_failed",
                                  "core reply admission could not be prepared")

    async def _huawei_health_material(self, context, at):
        if context.scope.kind is not ScopeKind.PRIVATE:
            return None
        try:
            settings = self.application.perception_settings.capture(context, at)["settings"]
            if settings.get("health_enabled") is not True:
                return None
            service = self.huawei_health
            await service.initialize()
            if not service.is_bound():
                return None
            if not service.allowed_for(context.actor.subject.subject_id,
                                       context.role_binding.role.subject_id):
                return None
            cached = service.cached_context(max_age_seconds=3600)
            if cached is None:
                service.schedule_refresh()
            return cached
        except Exception as error:
            logger.warning("[CompanionV2] 华为健康感知读取降级 error_type=%s", type(error).__name__)
            return None

    async def prepare_core_reply(self, event, target, *, group_admission=None, plan=None):
        """Prepare only V2 identity and prompt data for AstrBot's normal reply pipeline."""
        async with self._intake[target.target_id]:
            presence, typing_context, typing_turn, prepared_ok = None, None, None, False
            try:
                self.application.require_running()
                if plan is None:
                    captured = require(await (
                        self.bridge.capture_text_only(event)
                        if isinstance(target, GroupTarget)
                        else self.bridge.capture_core_reply(event)
                    ))
                    captured = await self._materialize_core_conversation(event, target, captured)
                    self._require_core_reply_target(target, captured)
                    context = self._select_core_reply_context(target, captured)
                else:
                    captured, context = plan.captured, plan.context
                    # A short debounce window can cross a persona/window change:
                    # re-resolve under the intake lock instead of trusting the plan.
                    self._require_core_reply_target(target, captured)
                    context = self._select_core_reply_context(target, captured)
                self._validate_core_group_admission(group_admission, context, captured)
                turn_id = "core-reply:" + digest([context.key, captured.event_id])
                presence = self.application.presence
                if presence is not None:
                    typing_context, typing_turn = context, turn_id
                    presence.begin_native_reply(context, turn_id)
                # The core request hook re-captures identity before provider dispatch.
                if context.scope.kind is ScopeKind.GROUP:
                    # Native media fallbacks carry placeholders; only original
                    # Plain components may become a member's memory evidence.
                    raw_text = "".join(part.text for part in getattr(event.message_obj, "message", ())
                        if getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) == "Plain"
                        and isinstance(getattr(part, "text", None), str))
                    captured = replace(captured, text=raw_text)
                segmentation_settings, presentation_settings = None, None
                group_tool_profile = "full"
                captured_voice_policy, voice_settings_available = None, False
                captured_outbound_settings = None
                astrbot_animated_image_understanding_enabled = False
                if not group_admission or group_admission.get("reason") != "repeat":
                    try:
                        from companion_v2.conversation.segmentation import SegmentationPolicy
                        from companion_v2.conversation.reply_settings import (
                            GROUP, OUTBOUND_DEFAULTS, VOICE_FIELDS, outbound_settings,
                            voice_policy as parse_voice_policy,
                        )
                        from companion_v2.conversation.text_processing import OUTPUT_TEXT_FIELDS
                        from dataclasses import fields
                        snapshot = self.application.reply_settings.snapshot(context.actor, context, self.now())
                        presentation_settings = {name: snapshot.values[GROUP + "." + name]
                                                 for name in OUTPUT_TEXT_FIELDS}
                        profile = snapshot.values.get(GROUP + ".group_tool_profile", "full")
                        group_tool_profile = profile if profile in ("lean", "standard", "full") else "full"
                        astrbot_animated_image_understanding_enabled = (
                            snapshot.values.get(GROUP + ".astrbot_animated_image_understanding_enabled") is True)
                        names = {field.name for field in fields(SegmentationPolicy)} | {
                            "streaming", "part_gap_mode", "part_gap_ms", "part_gap_min_ms",
                            "part_gap_max_ms", "part_gap_log_base"}
                        segmentation_settings = {name: snapshot.values[GROUP + "." + name] for name in names}
                        if context.scope.kind is ScopeKind.PRIVATE:
                            try:
                                from companion_v2.role_work.state import humanized
                                simulation = self.application.role_state.read(context, at=self.now(), injection=True)
                                values = simulation.get("values") if isinstance(simulation.get("values"), dict) else {}
                                if isinstance(values.get("energy"), int):
                                    segmentation_settings.setdefault("role_energy", values["energy"])
                                if humanized.is_sleepy(simulation):
                                    segmentation_settings.setdefault("role_sleepy", True)
                            except Exception:
                                pass
                        voice_names = ("voice_enabled", *("voice_" + name for name in VOICE_FIELDS))
                        voice_values = {name: snapshot.values[GROUP + "." + name] for name in voice_names}
                        captured_voice_policy = parse_voice_policy(voice_values)
                        outbound_values = {name: snapshot.values.get(
                            GROUP + "." + name, default) for name, default in OUTBOUND_DEFAULTS.items()}
                        try:
                            captured_outbound_settings = outbound_settings(outbound_values)
                        except Exception as error:
                            logger.warning("[CompanionV2] 核心回复出站设置无效，使用默认值 error_type=%s",
                                           type(error).__name__)
                            captured_outbound_settings = dict(OUTBOUND_DEFAULTS)
                        captured_outbound_settings["image_position"] = snapshot.values.get(
                            GROUP + ".image_position", "after_text")
                        voice_settings_available = True
                    except Exception as error:
                        logger.warning("[CompanionV2] 核心回复文字设置暂不可读，保留宿主发送 error_type=%s", type(error).__name__)
                prompt_instruction, stable_prompt_instruction = "", ""
                context_sections, expression_signal_ids = (), ()
                perception_options = None
                perception_context, perception_instructions, perception_guidance = "", (), ""
                context_materials, preparation_steps = (), ()
                try:
                    from .core_reply_context import build_core_reply_prompt
                    if not group_admission or group_admission.get("reason") != "repeat":
                        build_started_at = self.now()
                        keyword_entries = ()
                        registry = getattr(self.application, "keyword_materials", None)
                        if registry is not None:
                            try:
                                keyword_entries = await registry.prepare(
                                    context, getattr(captured, "text", "") or "", at=self.now())
                            except Exception as error:
                                logger.warning("[CompanionV2] 关键词材料未注入 error_type=%s",
                                               type(error).__name__)
                        health_material = await self._huawei_health_material(context, self.now())
                        from .external_memory import prepare_external_memory
                        external_memory = await prepare_external_memory(
                            self.bridge.context, self.application, context, captured)
                        prompt = build_core_reply_prompt(self.application, context, captured, at=self.now(),
                                                         keyword_materials=keyword_entries,
                                                         health_material=health_material,
                                                         external_memory=external_memory)
                        prompt_instruction = getattr(prompt, "context_instruction", prompt.instruction)
                        stable_prompt_instruction = getattr(prompt, "stable_instruction", "")
                        context_sections = prompt.sections
                        expression_signal_ids = getattr(prompt, "expression_signal_ids", ())
                        context_materials = getattr(prompt, "materials", ())
                        perception_options = getattr(prompt, "perception_options", None)
                        perception_context = getattr(prompt, "perception_context", "")
                        perception_instructions = getattr(prompt, "perception_instructions", ())
                        perception_guidance = getattr(prompt, "perception_guidance", "")
                        preparation_steps = ({"name": "context_build", "state": "returned",
                            "started_at": stamp(build_started_at), "finished_at": stamp(self.now()),
                            "elapsed_ms": getattr(prompt, "build_ms", None)},)
                except Exception as error:
                    logger.warning("[CompanionV2] 核心回复背景准备降级 target=%s error_type=%s",
                                   target.target_id, type(error).__name__)
                channel = "group" if context.scope.kind is ScopeKind.GROUP else "private"
                reaction_reply_enabled = False
                if not group_admission or group_admission.get("reason") != "repeat":
                    try:
                        from .core_reply_reaction_delivery import prepare_reaction_turn
                        reaction_reply_enabled = prepare_reaction_turn(
                            getattr(self.application.tools, "semantic", None), context, captured, turn_id)
                    except Exception as error:
                        logger.warning("[CompanionV2] 表情回复准备降级 error_type=%s", type(error).__name__)
                voice_policy, configured_voice_policy = None, None
                is_group_repeat = group_admission and group_admission.get("reason") == "repeat"
                if not is_group_repeat:
                    from companion_v2.conversation.voice_reply import eligible, voice_policy_for_turn
                    if voice_settings_available:
                        configured_voice_policy = captured_voice_policy
                    else:
                        common_settings = self.settings.common
                        configured_voice_policy = (common_settings.voice_reply_policy
                                                   if common_settings.voice_reply_enabled else None)
                    admin_override = None
                    if self._actor_is_core(context):
                        from .voice import admin_voice_override, tts_provider_catalog
                        admin_override = admin_voice_override(
                            configured_voice_policy, captured.text,
                            tts_provider_catalog(self.bridge.context))
                    if admin_override is not None:
                        configured_voice_policy = configured_voice_policy or admin_override
                        voice_policy = admin_override
                    elif configured_voice_policy is not None:
                        candidate = voice_policy_for_turn(configured_voice_policy, captured.text)
                        primary_mention = self._core_user_mention_matches(
                            event, context, candidate, captured.text, captured.at)
                        if eligible(candidate, input_text=captured.text, kind=channel, batch_id=turn_id,
                                     primary_mention=primary_mention):
                            if primary_mention:
                                mention_prompt = "主要用户的已认证账号在本轮原生消息中被提及，且正文命中定向语音词。"
                                extra_prompt = "\n".join(part for part in (
                                    candidate.extra_prompt.strip(), mention_prompt,
                                    candidate.primary_mention_prompt.strip()) if part)
                                candidate = replace(candidate, trigger_mode="adaptive", cooldown_seconds=0,
                                    private_cooldown_seconds=-1, group_cooldown_seconds=-1,
                                                    extra_prompt=extra_prompt)
                            voice_policy = candidate
                from .fish_audio import provider_direction
                voice_direction = await provider_direction(self.bridge.context, voice_policy, event.unified_msg_origin, channel)
                source_future = None
                if self.core_reply_observations is not None:
                    try:
                        source_future = self.core_reply_observations.input(context, captured, turn_id=turn_id,
                            expression_signal_ids=expression_signal_ids)
                    except Exception as error:
                        logger.warning("[CompanionV2] 入站后台观察调度降级 error_type=%s", type(error).__name__)
                try:
                    self.core_reply_maintenance.schedule(context, captured.instruction, source_future=source_future)
                except Exception as error:
                    logger.warning("[CompanionV2] 私聊后台绑定调度降级 error_type=%s", type(error).__name__)
                prepared_ok = True
                return Outcome.success({
                    "context": context,
                    "instruction": captured.instruction,
                    "prompt_instruction": prompt_instruction,
                    "stable_prompt_instruction": stable_prompt_instruction,
                    "target_id": target.target_id,
                    "channel": channel,
                    "event_id": captured.event_id,
                    "text": captured.text,
                    "context_sections": context_sections,
                    "perception_options": perception_options,
                    "perception_context": perception_context,
                    "perception_instructions": perception_instructions,
                    "perception_guidance": perception_guidance,
                    "context_materials": context_materials,
                    "preparation_steps": preparation_steps,
                    "expression_signal_ids": expression_signal_ids,
                    "turn_id": turn_id,
                    "native_input": captured,
                    "native_source_future": source_future,
                    "segmentation_settings": segmentation_settings,
                    "presentation_settings": presentation_settings,
                    "group_tool_profile": group_tool_profile,
                    "outbound_settings": captured_outbound_settings,
                    "astrbot_animated_image_understanding_enabled": astrbot_animated_image_understanding_enabled,
                    "voice_reply_policy": voice_policy,
                    "configured_voice_reply_policy": configured_voice_policy,
                    "voice_provider_instruction": voice_direction,
                    "reaction_reply_enabled": reaction_reply_enabled,
                })
            except TaskError as error:
                return CoreReplyOutcome.from_failure(error)
            except Exception as error:
                logger.error("[CompanionV2] 核心回复准入准备失败 target=%s error_type=%s",
                             target.target_id, type(error).__name__)
                return CoreReplyOutcome.failed("host_core_reply_prepare_failed", "core reply admission could not be prepared")
            finally:
                if not prepared_ok and presence is not None and typing_context is not None:
                    presence.finish_native_reply(typing_context, typing_turn)

    def _actor_is_core(self, context):
        try:
            subject = context.actor.subject
            row = self.runtime.read_one(
                "SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                (subject.kind.value, subject.subject_id))
            return bool(row and row["tier"] == AuthorizationTier.CORE.value)
        except Exception:
            return False

    def _core_user_mention_matches(self, event, context, policy, text, at):
        words = policy.primary_mention_words()
        if not words or policy.primary_mention_probability <= 0:
            return False
        compact = text.casefold()
        if not any(word in compact for word in words):
            return False
        scope = context.scope
        for account_id in mentioned_account_ids(event):
            principal = AuthenticatedHostPrincipal(scope.installation_id, HOST_ID, scope.platform,
                account_id, scope.bot_id, scope.account_id)
            try:
                resolved = self.authority.identities.resolve_authenticated(principal, observed_at=at)
                if not resolved.is_success:
                    continue
                subject = resolved.unwrap().subject
                tier = self.runtime.read_one(
                    "SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                    (subject.kind.value, subject.subject_id))
                if tier is not None and tier["tier"] == AuthorizationTier.CORE.value:
                    return True
            except Exception:
                continue
        return False

    async def authorize_core_reply(self, event, target, prepared):
        """Re-capture the live route and permission immediately before core send."""
        async with self._intake[target.target_id]:
            try:
                self.application.require_running()
                if isinstance(target, GroupTarget):
                    from companion_v2.conversation.group_llm_block import GroupLLMBlock
                    if GroupLLMBlock(self.application.runtime, now=self.now()).status_for_target(target) is not None:
                        raise TaskError("group_llm_blocked",
                                        "model replies are temporarily blocked in this group")
                if not isinstance(prepared, Mapping):
                    raise TaskError("core_reply_context_invalid", "prepared core reply context is unavailable")
                if prepared.get("target_id") != target.target_id:
                    raise TaskError("core_reply_target_changed", "core reply target changed after admission")
                captured = require(await (
                    self.bridge.capture_text_only(event)
                    if isinstance(target, GroupTarget)
                    else self.bridge.capture_core_reply(event)
                ))
                current_target = self.bridge.target_for_scope(captured.route.scope)
                if current_target != target:
                    raise TaskError("host_target_mismatch", "core reply route changed after admission")
                if prepared.get("event_id") != captured.event_id:
                    raise TaskError("core_reply_event_changed", "core reply event changed after admission")
                if prepared.get("instruction") != captured.instruction:
                    raise TaskError("core_reply_instruction_changed", "core reply persona instruction changed after admission")

                actor = require(self.authority.identities.resolve_authenticated(
                    captured.principal, observed_at=self.now()))
                persona = require(self.authority.personas.resolve(
                    captured.route.scope, observed_at=self.now()))
                if persona.binding.role != captured.role:
                    raise TaskError("host_persona_changed", "current native persona differs from the captured role")
                current = TaskContext(actor, persona.binding, captured.recipients)
                original = prepared.get("context")
                if not isinstance(original, TaskContext) or current.key != original.key:
                    raise TaskError("core_reply_context_changed", "identity, route or role changed after admission")
                if self.application.messages.recalls.recalled(
                        current.scope, captured.event_id, captured.principal.native_account_id):
                    silence = True
                    if self.application.reply_settings is not None:
                        try:
                            silence = self.application.reply_settings.capture(
                                current, self.now())["settings"].get("recall_silence_enabled", True)
                        except Exception:
                            silence = True
                    if silence:
                        raise TaskError("message_withdrawn",
                                        "the trigger message was withdrawn before delivery")
                # capture_* above already resolves the live role for this authorization pass.
                if current.scope.kind is ScopeKind.PRIVATE:
                    if self.application.private_access_gate.denied(current, at=self.now()):
                        raise TaskError("private_access_dialogue_denied",
                                        "this private user no longer has dialogue permission")
                    self.authority.delivery_access(target)
                else:
                    require(self.application.tasks.check_context_access(
                        current, action="conversation.reply", observed_at=self.now()))
                    self.authority.group_delivery.access(target)
                return Outcome.success(True)
            except TaskError as error:
                return Outcome.failed(error.code, str(error))
            except Exception as error:
                logger.error("[CompanionV2] 核心回复发前复核失败 target=%s error_type=%s",
                             target.target_id, type(error).__name__)
                return Outcome.failed("host_core_reply_authorize_failed", "core reply authorization failed")

    async def plan_calendar(self, event, target, *, mode, refresh=False):
        from companion_v2.foundation.refs import ScopeKind

        async with self._intake[target.target_id]:
            self.application.require_running()
            captured = require(await self.bridge.capture(event))
            if (captured.route.scope.kind is not ScopeKind.PRIVATE
                    or self.bridge.target_for_scope(captured.route.scope) != target):
                raise TaskError("host_target_mismatch", "calendar generation requires its configured private target")
            context = self.authority.select(target, captured.route.scope, captured.role)
            if self.application.private_access_gate.denied(context, at=self.now()):
                raise TaskError("private_access_dialogue_denied", "this private user has no dialogue permission")
            binding = self.application.state_maintenance.bindings.get(context.key)
            if binding is None:
                raise TaskError("planning_role_changed", "original role binding is unavailable")
            return await self.application.calendar_planning.run(context, binding[1], mode=mode,
                slot="manual:" + mode + ":" + captured.event_id, manual=True, refresh=refresh)

    def _record_platform_names(self, event, context):
        """Record platform display names from the already authenticated OneBot
        event. Optional derivation: any failure only costs a name, never intake."""
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            sender = raw.get("sender") if isinstance(raw, Mapping) else None
            if not isinstance(sender, Mapping):
                return
            group = context.scope.kind is ScopeKind.GROUP
            if not group and context.scope.native_scope_id != context.actor.principal.native_account_id:
                return
            self.application.messages.acquaintance.record_platform_names(
                context, group_id=context.scope.native_scope_id if group else None,
                nickname=sender.get("nickname"), card=sender.get("card"))
            if group:
                # Keep platform names in the same group relationship projection
                # as the acquaintance card; both remain display clues, never
                # identity proof.
                self.application.messages.group_network.record_names(
                    context, nickname=sender.get("nickname"), card=sender.get("card"), at=self.now())
        except Exception:
            pass

    def authorize(self, context):
        self.application.require_running()
        target = self.bridge.target_for_scope(context.scope)
        require(self.application.tasks.check_context_access(context, action="conversation.reply", observed_at=self.now()))
        return (self.authority.group_delivery.access(target) if context.scope.kind is ScopeKind.GROUP
                else self.authority.delivery_access(target))

    def authorize_group(self, envelope):
        self.authorize(envelope.context)
        target = self.bridge.target_for_scope(envelope.context.scope)
        return self.authority.group_delivery.authorize_envelope(target, envelope)

    async def send(self, envelope):
        # 正式版：不再有"预览/草稿"这一档，也不再需要投递授权——一律真发。
        group = envelope.context.scope.kind is ScopeKind.GROUP
        platform_name = self.bridge.platform_name(envelope.context.scope)
        if platform_name == "aiocqhttp":
            sender_type = OneBotGroupSender if group else OneBotPrivateSender
        else:
            sender_type = AstrBotPlatformSender
        sender = sender_type(self.bridge, binding=self.sender_binding, version=self.types.version,
            reverse_api_type=self.types.reverse_api,
            authorize=lambda: self.authorize_group(envelope) if group else self.authorize(envelope.context), now=self.now,
            voice=self.voice, voice_delivery=self.voice_delivery,
            reaction_image_format=lambda: self.application.semantics.store.settings()["reactions_image_format"]
                if self.application.semantics is not None else "image")
        return await sender.send(envelope)

    async def close(self, *, grace_seconds=0.5):
        await self.core_reply_maintenance.close()
        from .core_reply_tools import close_core_reply_tools
        await close_core_reply_tools(self.application)
        observer = getattr(self, "core_reply_observations", None)
        if observer is not None:
            await observer.close()
        if callable(getattr(self.native_tool_port, "aclose", None)):
            await self.native_tool_port.aclose()
        elif callable(getattr(self.native_tool_port, "close", None)):
            self.native_tool_port.close()
        return await self.application.close(grace_seconds=grace_seconds)
