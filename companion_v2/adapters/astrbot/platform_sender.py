"""AstrBot-native delivery for non-OneBot platforms such as QQ Official.

The platform adapter, not this plugin, owns the transport payload.  This
sender serializes the already-sealed V2 component plan into AstrBot's
``MessageChain`` and delegates the actual send to
``Platform.send_by_session``.  That keeps QQ Official login, markdown,
C2C/group openids and media upload rules inside AstrBot.
"""

import asyncio
from datetime import datetime, timezone
import logging
from time import perf_counter
from uuid import uuid4

from astrbot.core.message.components import At, Face, Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.delivery.components import build_component_plan
from companion_v2.delivery.media import ImageDeliveryEnvelope, payload_hash
from companion_v2.delivery.models import DeliveryEnvelope
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from .telegram import PLATFORM_NAME as TELEGRAM_PLATFORM_NAME


logger = logging.getLogger(__name__)


class TelegramVoiceRecord(Record):
    """A ``Record`` whose host conversion returns an already-valid voice file.

    AstrBot's Telegram adapter calls ``Record.convert_to_file_path()`` before
    ``send_voice``, and that conversion always re-encodes to WAV, which
    Telegram rejects as a voice note.  This subclass keeps the normal Record
    surface but returns the prepared OGG/MP3/M4A path unchanged.
    """

    def __init__(self, file, *, path, text=None):
        super().__init__(file=file, text=text)
        self._telegram_voice_path = path

    async def convert_to_file_path(self):
        return self._telegram_voice_path


class AstrBotPlatformSender:
    """Send through AstrBot's public session transport for one admitted route."""

    supports_images = True

    def __init__(self, bridge, *, binding, version, reverse_api_type, authorize, now=None, voice=None,
                 voice_delivery=None, reaction_image_format=None, voice_converter=None):
        # version/reverse_api_type are accepted for the common sender factory.
        del version, reverse_api_type, reaction_image_format
        if not callable(authorize):
            raise ValueError("platform delivery requires a current authorization callback")
        self.bridge, self.binding = bridge, binding
        self.authorize = authorize
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.voice, self.voice_delivery = voice, voice_delivery
        # Trusted host hook: convert rendered TTS audio into a Telegram voice
        # note format (OGG/OPUS/MP3/M4A) and return a local path.
        self.voice_converter = voice_converter if callable(voice_converter) else None
        # Telegram needs usernames on outbound At components; cache per target.
        self._telegram_mention_cache = {}

    def local_sender(self):
        return LocalSender(self.binding, self.send, supports_images=self.supports_images, supports_poke=False)

    def _receipt(self, envelope, status, platform_ref=None, failure_code=None):
        return DeliveryReceipt(
            envelope.part_id, envelope.operation_id, envelope.context.key,
            envelope.payload_hash, self.binding, status,
            "astrbot-session-ack-" + uuid4().hex, platform_ref,
            self.now() if status == "delivered" else None, failure_code,
        )

    def _session(self, scope):
        kind = MessageType.GROUP_MESSAGE if scope.kind is ScopeKind.GROUP else MessageType.FRIEND_MESSAGE
        session_id = scope.session_id if scope.kind is ScopeKind.GROUP else scope.native_scope_id
        return MessageSession(scope.platform, kind, session_id)

    @staticmethod
    def _chain(component_plan, *, telegram=False, mention_names=None):
        mention_names = mention_names or {}
        chain = []
        for component in component_plan.components:
            if component.kind == "text":
                chain.append(Plain(text=component.value))
            elif component.kind == "image":
                chain.append(Image.fromBase64(component.value.data_base64))
            elif component.kind == "voice":
                value = component.value
                data = value.get("data") if isinstance(value, dict) else None
                file_value = data.get("file") if isinstance(data, dict) else None
                if not isinstance(file_value, str) or not file_value.strip():
                    raise ValueError("voice component has no native file")
                if telegram:
                    chain.append(TelegramVoiceRecord(file=file_value, path=file_value))
                else:
                    chain.append(Record(file=file_value))
            elif component.kind == "at":
                if telegram:
                    # Telegram renders mentions from usernames; an id-only At
                    # is silently ignored by the adapter, so omit it instead.
                    name = mention_names.get(str(component.value))
                    if not name:
                        continue
                    chain.append(At(qq=str(component.value), name=name))
                else:
                    chain.append(At(qq=str(component.value)))
            elif component.kind == "face":
                if telegram:
                    # Telegram has no QQ face; keep the text instead of failing.
                    continue
                chain.append(Face(id=int(component.value)))
            else:
                raise ValueError("component kind is unsupported by the AstrBot session transport")
        return MessageChain(chain=chain)

    async def _telegram_mention_names(self, platform, component_plan):
        """Resolve Telegram usernames for outbound At targets (best effort)."""
        names = {}
        getter = getattr(platform.get_client(), "get_chat", None)
        if not callable(getter):
            return names
        for component in component_plan.components:
            if component.kind != "at":
                continue
            value = str(component.value)
            if value in self._telegram_mention_cache:
                cached = self._telegram_mention_cache[value]
                if cached:
                    names[value] = cached
                continue
            try:
                chat = await asyncio.wait_for(getter(chat_id=int(value)), timeout=2.0)
            except Exception:
                self._telegram_mention_cache[value] = ""
                continue
            username = getattr(chat, "username", None)
            username = username if isinstance(username, str) else ""
            self._telegram_mention_cache[value] = username
            if username:
                names[value] = username
        return names

    async def _render_voice(self, envelope, scope, context):
        if self.voice is None or not envelope.text:
            return None, None, "not_applicable"
        if envelope.voice is not None:
            plan = {
                "speech": envelope.voice.speech,
                "provider_id": envelope.voice.provider_id,
                "timeout_seconds": envelope.voice.timeout_seconds,
                "position": envelope.voice.position,
                "delivery_mode": envelope.voice.delivery_mode,
            }
            try:
                segment = await self.voice(
                    context, plan["speech"], provider_id=plan["provider_id"],
                    timeout_seconds=plan["timeout_seconds"])
            except Exception:
                return None, None, "failed"
            return plan, segment, "ready" if segment is not None else "failed"
        if self.voice_delivery is None:
            plan = {
                "speech": envelope.text,
                "provider_id": "",
                "timeout_seconds": 30,
                "position": "after_media",
                "delivery_mode": "voice_only" if envelope.voice_required else "voice_and_text",
            }
            try:
                segment = await self.voice(context, envelope.text)
            except Exception:
                return plan, None, "failed"
            return plan, segment, "ready" if segment is not None else "failed"
        plan = self.voice_delivery.claim(envelope)
        if plan is None:
            return None, None, "cooldown"
        try:
            segment = await self.voice(
                context, plan["speech"], provider_id=plan["provider_id"],
                timeout_seconds=plan["timeout_seconds"])
        except Exception:
            return plan, None, "failed"
        return plan, segment, "ready" if segment is not None else "failed"

    async def send(self, envelope):
        started = perf_counter()
        stage = "envelope_validation"
        voice_plan = None

        def not_sent(reason):
            logger.warning("V2 platform delivery not sent part=%s stage=%s reason=%s elapsed_ms=%d",
                           getattr(envelope, "part_id", "unknown"), stage, reason,
                           round((perf_counter() - started) * 1000))
            if voice_plan is not None and envelope.voice is None and self.voice_delivery is not None:
                self.voice_delivery.release(voice_plan, envelope.part_id)
            return self._receipt(envelope, "not_sent", failure_code=reason)

        try:
            context, scope = envelope.context, envelope.context.scope
            principal = context.actor.principal
            if type(envelope) not in (DeliveryEnvelope, ImageDeliveryEnvelope):
                return not_sent("unsupported_envelope_type")
            images = envelope.images if isinstance(envelope, ImageDeliveryEnvelope) else ()
            image_position = envelope.image_position if isinstance(envelope, ImageDeliveryEnvelope) else "after_text"
            if images:
                envelope.__post_init__()
            has_message_content = bool(envelope.text.strip()) or bool(images)
            if (envelope.sender != self.binding
                    or payload_hash(envelope.text, images, image_position=image_position) != envelope.payload_hash
                    or (not has_message_content and envelope.poke is None)
                    or (not envelope.text.strip() and envelope.text != "" and not images)
                    or len(envelope.text) > 65536
                    or principal.host_id != self.bridge.host_id
                    or principal.installation_id != scope.installation_id
                    or principal.platform != scope.platform
                    or principal.bot_id != scope.bot_id
                    or principal.account_id != scope.account_id):
                return not_sent("envelope_binding_mismatch")
            if envelope.poke is not None:
                # QQ Official has no OneBot poke action. Text/images remain valid;
                # the best-effort poke is omitted rather than failing the reply.
                logger.info("V2 platform delivery omitted unsupported poke part=%s", envelope.part_id)
            stage = "route_authorization"
            authorization = self.authorize()
            if isinstance(authorization, Outcome):
                require(authorization)
            route_bridge = (getattr(self.bridge, "groups", self.bridge)
                            if scope.kind is ScopeKind.GROUP else self.bridge)
            await route_bridge.verify_role(scope, context.role_binding)
            platform = route_bridge.platform(scope)
            route_bridge.client(scope)
            if not callable(getattr(platform, "send_by_session", None)):
                return not_sent("platform_session_transport_unavailable")

            session = self._session(scope)
            stage = "voice_render"
            voice_plan, voice_segment, render_state = await self._render_voice(envelope, scope, context)
            platform_name = str(getattr(getattr(platform, "meta", lambda: None)(), "name", "") or "")
            telegram = platform_name == TELEGRAM_PLATFORM_NAME
            if telegram and voice_plan is not None and voice_segment is not None:
                data = voice_segment.get("data") if isinstance(voice_segment, dict) else None
                file_value = data.get("file") if isinstance(data, dict) else None
                converted = (await self.voice_converter(file_value)
                             if isinstance(file_value, str) and self.voice_converter is not None else None)
                if converted is None:
                    if envelope.voice_required:
                        return not_sent("required_voice_unavailable")
                    if self.voice_delivery is not None and envelope.voice is None:
                        self.voice_delivery.release(voice_plan, envelope.part_id)
                    voice_plan, voice_segment = None, None
                else:
                    voice_segment = {**voice_segment, "data": {**data, "file": converted}}
            if voice_plan is not None and voice_segment is None:
                if envelope.voice_required:
                    return not_sent("required_voice_unavailable")
                if self.voice_delivery is not None and envelope.voice is None:
                    self.voice_delivery.release(voice_plan, envelope.part_id)
                voice_plan = None
            stage = "message_preparation"
            component_plan = (build_component_plan(
                envelope.text,
                images,
                voice_segment,
                position=voice_plan["position"] if voice_plan is not None else "after_media",
                image_position=image_position,
                voice_only=(voice_plan is not None and voice_plan["delivery_mode"] == "voice_only"),
                speech=voice_plan["speech"] if voice_plan is not None else None,
                order=envelope.plan.order if envelope.plan is not None else (),
                mentions=envelope.plan.mentions if envelope.plan is not None else (),
                faces=envelope.plan.faces if envelope.plan is not None else (),
            ) if has_message_content else None)
            if voice_plan is not None and component_plan is not None and not component_plan.voice_included:
                if envelope.voice_required:
                    return not_sent("required_voice_unavailable")
                if self.voice_delivery is not None and envelope.voice is None:
                    self.voice_delivery.release(voice_plan, envelope.part_id)
                voice_plan = None
            if self.voice_delivery is not None and envelope.voice is None:
                self.voice_delivery.record(
                    envelope.part_id, render_state,
                    component_plan.kinds if component_plan is not None else (),
                    component_plan.text_hidden if component_plan is not None else False)
            if not has_message_content:
                return not_sent("empty_message")
            mention_names = (await self._telegram_mention_names(platform, component_plan)
                             if telegram else {})
            chain = self._chain(component_plan, telegram=telegram, mention_names=mention_names)

            # Re-read the live route and authorization immediately before the
            # platform-owned side effect. This mirrors the OneBot dispatch fence.
            stage = "final_dispatch_check"
            authorization = self.authorize()
            if isinstance(authorization, Outcome):
                require(authorization)
            await route_bridge.verify_role(scope, context.role_binding)
            route_bridge.client(scope)
            if envelope.before_dispatch is None:
                return not_sent("dispatch_fence_missing")
            envelope.before_dispatch()
            stage = "platform_send"
            try:
                await platform.send_by_session(session, chain)
            except Exception as error:
                logger.warning("V2 platform delivery outcome unknown part=%s error_type=%s",
                               envelope.part_id, type(error).__name__)
                return self._receipt(envelope, "unknown")
            platform_ref = "astrbot-session:" + digest([
                scope.platform, scope.account_id, scope.session_id, envelope.part_id,
            ])
            if self.voice_delivery is not None and envelope.voice is None and voice_plan is not None:
                self.voice_delivery.record(
                    envelope.part_id, "ready", component_plan.kinds, component_plan.text_hidden)
            return self._receipt(envelope, "delivered", platform_ref)
        except asyncio.CancelledError:
            raise
        except TaskError as error:
            return not_sent(error.code)
        except Exception:
            return not_sent("platform_delivery_failed")
