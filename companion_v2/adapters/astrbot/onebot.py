"""Pinned OneBot reverse-WebSocket delivery with actual API message references."""

import asyncio
from datetime import datetime, timezone
import logging
import re
from time import perf_counter
from uuid import uuid4

from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.conversation.quotes.models import message_number
from companion_v2.delivery.components import build_component_plan
from companion_v2.delivery.models import DeliveryEnvelope
from companion_v2.delivery.media import ImageDeliveryEnvelope, payload_hash
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind

from .native import account_number, check_version


logger = logging.getLogger(__name__)
_EVENTCHECKER_REJECTION = "qq_eventchecker_1200"


def _eventchecker_rejection(value):
    if isinstance(value, dict):
        retcode = value.get("retcode")
        detail = " ".join(str(value.get(key, "")) for key in ("message", "msg", "wording", "status"))
        if retcode in (1200, "1200") and "eventchecker" in detail.lower():
            return _EVENTCHECKER_REJECTION
    text = str(value).lower()
    if re.search(r"retcode\s*[=:：]\s*1200", text) and "eventchecker" in text:
        return _EVENTCHECKER_REJECTION
    return None


class OneBotTextSender:
    """Shared reverse-WS transport; subclasses fix the audience and API action."""

    supports_images = False

    def __init__(self, bridge, *, binding, version, reverse_api_type, authorize, now=None, voice=None,
                 voice_delivery=None, reaction_image_format=None, voice_converter=None):
        # OneBot sends the rendered voice as-is; the Telegram-only converter is
        # accepted for the shared sender factory and intentionally ignored.
        del voice_converter
        check_version(version)
        if not callable(authorize):
            raise ValueError("OneBot delivery requires a current authorization callback")
        self.bridge, self.binding, self.reverse_api_type = bridge, binding, reverse_api_type
        self.authorize = authorize
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.voice = voice
        self.voice_delivery = voice_delivery
        self.reaction_image_format = reaction_image_format or (lambda: "image")

    def local_sender(self):
        return LocalSender(self.binding, self.send, supports_images=self.supports_images, supports_poke=True)

    async def _final_dispatch_check(self, envelope, scope, context):
        """Re-read every live boundary immediately before a native API call.

        The earlier preflight protects model and voice work, but those steps can
        await while the host account, persona, conversation, permissions, or
        group admission changes.  Keep this check adjacent to ``call_action``;
        a returned API handle is used without another await before dispatch.
        """
        authorization = self.authorize()
        if isinstance(authorization, Outcome):
            require(authorization)
        await self.bridge.verify_role(scope, context.role_binding)
        client = self.bridge.client(scope)
        api = client._api._wsr_api
        if (type(api) is not self.reverse_api_type or api._api_clients is not client._wsr_api_clients
                or not client._wsr_api_clients.get(scope.account_id)):
            raise ValueError("original account connection is unavailable")
        if envelope.before_dispatch is None:
            raise ValueError("delivery envelope has no dispatch fence")
        # Recheck withdrawal, task ownership, sender binding and group admission
        # after all host awaits and immediately before the native side effect.
        envelope.before_dispatch()
        return api

    async def send(self, envelope):
        started = perf_counter()

        def receipt(status, platform_ref=None, failure_code=None):
            return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key,
                envelope.payload_hash, self.binding, status, "onebot-ack-" + uuid4().hex,
                platform_ref, self.now() if status == "delivered" else None, failure_code)

        stage = "envelope_validation"

        def not_sent(reason):
            logger.warning("V2 OneBot delivery not sent part=%s stage=%s reason=%s elapsed_ms=%d",
                           getattr(envelope, "part_id", "unknown"), stage, reason,
                           round((perf_counter() - started) * 1000))
            return receipt("not_sent")

        voice_plan = None
        voice_segment = None
        voice_included = False
        poke_outcome = None
        render_state = "unavailable" if self.voice is None else "not_applicable"
        try:
            context, scope = envelope.context, envelope.context.scope
            principal = context.actor.principal
            if type(envelope) not in (DeliveryEnvelope, ImageDeliveryEnvelope):
                return not_sent("unsupported_envelope_type")
            images = envelope.images if isinstance(envelope, ImageDeliveryEnvelope) else ()
            image_position = envelope.image_position if isinstance(envelope, ImageDeliveryEnvelope) else "after_text"
            plan = envelope.plan
            has_message_content = bool(envelope.text.strip()) or bool(images)
            poke_only = not has_message_content and envelope.poke is not None
            if images:
                if not self.supports_images:
                    return not_sent("unsupported_images")
                envelope.__post_init__()
            if (envelope.sender != self.binding or payload_hash(envelope.text, images, image_position=image_position) != envelope.payload_hash
                    or (not has_message_content and not poke_only)
                    or (not envelope.text.strip() and envelope.text != "" and not images)
                    or len(envelope.text) > 65536
                    or principal.host_id != self.bridge.host_id
                    or principal.installation_id != scope.installation_id or principal.platform != scope.platform
                    or principal.bot_id != scope.bot_id or principal.account_id != scope.account_id):
                return not_sent("envelope_binding_mismatch")
            stage = "route_authorization"
            action, target = self.route(context)
            authorization = self.authorize()
            if isinstance(authorization, Outcome):
                require(authorization)
            if self.voice is not None and envelope.text:
                stage = "persona_preflight"
                await self.bridge.verify_role(scope, context.role_binding)
            client = self.bridge.client(scope)
            api = client._api._wsr_api
            if (type(api) is not self.reverse_api_type or api._api_clients is not client._wsr_api_clients
                    or not client._wsr_api_clients.get(scope.account_id)):
                return not_sent("original_account_disconnected")
            if envelope.before_dispatch is None:
                return not_sent("dispatch_fence_missing")
            if self.voice is not None and envelope.text:
                stage = "voice_render"
                try:
                    if envelope.voice is not None:
                        # A sealed proactive presentation: the owner re-read this
                        # plan from its own record, so no per-part claim is needed.
                        voice_plan = {"speech": envelope.voice.speech, "provider_id": envelope.voice.provider_id,
                                      "timeout_seconds": envelope.voice.timeout_seconds,
                                      "position": envelope.voice.position,
                                      "delivery_mode": envelope.voice.delivery_mode}
                        render_state = "empty"
                        voice_segment = await self.voice(context, voice_plan["speech"],
                            provider_id=voice_plan["provider_id"], timeout_seconds=voice_plan["timeout_seconds"])
                    elif self.voice_delivery is None:
                        voice_plan = {"speech": envelope.text, "provider_id": "", "timeout_seconds": 30,
                                      "position": "after_media",
                                      "delivery_mode": "voice_only" if envelope.voice_required else "voice_and_text"}
                        voice_segment = await self.voice(context, envelope.text)
                    else:
                        voice_plan = self.voice_delivery.claim(envelope)
                        if voice_plan is not None:
                            render_state = "empty"
                            voice_segment = await self.voice(context, voice_plan["speech"],
                                provider_id=voice_plan["provider_id"], timeout_seconds=voice_plan["timeout_seconds"])
                        else:
                            render_state = "cooldown"
                except Exception as error:
                    render_state = "failed"
                    logger.warning("V2 voice rendering unavailable: %s", type(error).__name__)
            authorization = self.authorize()
            if isinstance(authorization, Outcome):
                require(authorization)
            stage = "message_preparation"
            envelope.before_dispatch()
            component_plan = (build_component_plan(
                envelope.text,
                images,
                voice_segment,
                position=voice_plan["position"] if voice_plan is not None else "after_media",
                image_position=image_position,
                voice_only=(voice_plan is not None and voice_plan["delivery_mode"] == "voice_only"),
                speech=voice_plan["speech"] if voice_plan is not None else None,
                order=plan.order if plan is not None else (),
                mentions=plan.mentions if plan is not None else (),
                faces=plan.faces if plan is not None else (),
            ) if has_message_content else None)
            voice_included = component_plan.voice_included if component_plan is not None else False
            if voice_plan is not None and voice_segment is not None:
                render_state = "ready" if voice_included else "invalid"
            if self.voice_delivery is not None and envelope.voice is None:
                self.voice_delivery.record(envelope.part_id, render_state,
                    component_plan.kinds if component_plan is not None else (),
                    component_plan.text_hidden if component_plan is not None else False)
            if envelope.voice_required and not voice_included:
                if self.voice_delivery is not None and voice_plan is not None and envelope.voice is None:
                    self.voice_delivery.release(voice_plan, envelope.part_id)
                return not_sent("required_voice_unavailable")
            message = []
            if has_message_content and plan is not None and plan.quote:
                message.append({"type": "reply", "data": {"id": plan.quote}})
            for component in (component_plan.components if component_plan is not None else ()):
                if component.kind == "text":
                    message.append({"type": "text", "data": {"text": component.value}})
                elif component.kind == "image":
                    data = {"file": "base64://" + component.value.data_base64}
                    if (component.value.parent.owner == "expression.reactions"
                            and self.reaction_image_format() == "qq_emoji"):
                        data["sub_type"] = 1
                    message.append({"type": "image", "data": data})
                elif component.kind == "at":
                    message.append({"type": "at", "data": {"qq": component.value}})
                elif component.kind == "face":
                    message.append({"type": "face", "data": {"id": str(component.value)}})
                else:
                    # The component plan has already checked the native record shape.
                    message.append(component.value)
            if envelope.poke is not None:
                stage = "poke_preflight"
                api = await self._final_dispatch_check(envelope, scope, context)
                poke_status = await self._poke(api, context, envelope.poke)
                poke_outcome = poke_status
                if poke_only:
                    if poke_status == "delivered":
                        return receipt("delivered", "onebot-poke:" + digest([envelope.part_id, context.key]))
                    if poke_status == "not_sent":
                        return not_sent("poke_not_sent")
                    return receipt(poke_status)
        except asyncio.CancelledError:
            if voice_plan is not None and envelope.voice is None and self.voice_delivery is not None:
                self.voice_delivery.release(voice_plan, envelope.part_id)
            raise
        except Exception as error:
            if voice_plan is not None and envelope.voice is None and self.voice_delivery is not None:
                self.voice_delivery.release(voice_plan, envelope.part_id)
            reason = error.code if isinstance(error, TaskError) else type(error).__name__
            return not_sent(reason)
        stage = "final_dispatch_check"
        try:
            api = await self._final_dispatch_check(envelope, scope, context)
        except asyncio.CancelledError:
            if voice_plan is not None and envelope.voice is None and self.voice_delivery is not None:
                self.voice_delivery.release(voice_plan, envelope.part_id)
            raise
        except Exception as error:
            if voice_plan is not None and envelope.voice is None and self.voice_delivery is not None:
                self.voice_delivery.release(voice_plan, envelope.part_id)
            reason = error.code if isinstance(error, TaskError) else type(error).__name__
            if poke_outcome in ("delivered", "unknown"):
                logger.warning("V2 OneBot delivery result unknown part=%s stage=%s reason=%s prior_poke=%s elapsed_ms=%d",
                               getattr(envelope, "part_id", "unknown"), stage, reason, poke_outcome,
                               round((perf_counter() - started) * 1000))
                return receipt("unknown")
            return not_sent(reason)
        stage = "platform_send"
        leading_voice_outcome = None
        try:
            # QQ record messages cannot reliably carry text or images. A mixed
            # request can return a message id while silently losing the text.
            # The required content gets its own ACK; voice remains best effort.
            optional_voice = None
            if voice_included and any(item["type"] not in ("record", "reply") for item in message):
                optional_voice = next(item for item in message if item["type"] == "record")
                record_index = next(index for index, item in enumerate(message) if item["type"] == "record")
                text_index = next((index for index, item in enumerate(message) if item["type"] == "text"), None)
                before_text = (record_index < text_index if text_index is not None
                               else next(item["type"] for item in message if item["type"] != "reply") == "record")
                message = [item for item in message if item["type"] != "record"]
                if before_text:
                    leading_voice_outcome = await self._send_optional_voice(
                        envelope, scope, context, action, target, optional_voice)
                    stage = "final_dispatch_check"
                    api = await self._final_dispatch_check(envelope, scope, context)
                    stage = "platform_send"
            # Direct reverse-WS call: never use the host boolean result or its HTTP/account fallback.
            result = await api.call_action(action, self_id=scope.account_id,
                **target, message=message)
            rejection = _eventchecker_rejection(result)
            if rejection is not None:
                logger.warning("V2 OneBot proactive delivery rejected part=%s failure_code=%s elapsed_ms=%d",
                               getattr(envelope, "part_id", "unknown"), rejection,
                               round((perf_counter() - started) * 1000))
                if leading_voice_outcome in ("delivered", "unknown") or poke_outcome in ("delivered", "unknown"):
                    return receipt("unknown")
                return receipt("not_sent", failure_code=rejection)
            # NapCat can return signed message IDs; account IDs remain positive.
            message_id = message_number(result["message_id"])
            if message_id in ("-1", "0"):
                return receipt("unknown")
            confirmed = receipt("delivered", "onebot-message:" + digest([scope.platform, scope.account_id])[:24] + ":" + message_id)
            if optional_voice is not None and not before_text:
                try:
                    await self._send_optional_voice(envelope, scope, context, action, target, optional_voice)
                except asyncio.CancelledError:
                    # Required text is already confirmed. An interrupted optional
                    # voice must not turn it into an unknown/replayable send.
                    logger.info("V2 OneBot optional voice interrupted after confirmed text part=%s", envelope.part_id)
            return confirmed
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Once dispatch was attempted, a timeout/error cannot prove non-delivery.
            rejection = _eventchecker_rejection(error)
            if rejection is not None:
                logger.warning("V2 OneBot proactive delivery rejected part=%s failure_code=%s elapsed_ms=%d",
                               getattr(envelope, "part_id", "unknown"), rejection,
                               round((perf_counter() - started) * 1000))
                if leading_voice_outcome in ("delivered", "unknown") or poke_outcome in ("delivered", "unknown"):
                    return receipt("unknown")
                return receipt("not_sent", failure_code=rejection)
            reason = error.code if isinstance(error, TaskError) else type(error).__name__
            if (stage == "final_dispatch_check" and leading_voice_outcome not in ("delivered", "unknown")
                    and poke_outcome not in ("delivered", "unknown")):
                return not_sent(reason)
            logger.warning("V2 OneBot delivery result unknown part=%s stage=%s reason=%s elapsed_ms=%d",
                           getattr(envelope, "part_id", "unknown"), stage, reason,
                           round((perf_counter() - started) * 1000))
            return receipt("unknown")
        finally:
            if (not voice_included and voice_plan is not None and envelope.voice is None
                    and self.voice_delivery is not None):
                self.voice_delivery.release(voice_plan, envelope.part_id)

    async def _send_optional_voice(self, envelope, scope, context, action, target, voice):
        """One independent voice attempt; its outcome never proves text delivery."""
        stage, status, message_id = "authorization", "not_sent", None
        try:
            api = await self._final_dispatch_check(envelope, scope, context)
            stage, status = "platform_send", "unknown"
            result = await api.call_action(action, self_id=scope.account_id, **target, message=[voice])
            if _eventchecker_rejection(result) is not None:
                status = "not_sent"
            else:
                message_id = message_number(result["message_id"])
                if message_id not in ("-1", "0"):
                    status = "delivered"
        except asyncio.CancelledError:
            logger.info("V2 OneBot optional voice interrupted part=%s stage=%s status=%s",
                        envelope.part_id, stage, status)
            raise
        except Exception as error:
            if _eventchecker_rejection(error) is not None:
                status = "not_sent"
            logger.warning("V2 OneBot optional voice unavailable part=%s stage=%s status=%s error_type=%s",
                           envelope.part_id, stage, status, type(error).__name__)
        logger.info("V2 OneBot optional voice receipt part=%s status=%s message_id=%s",
                    envelope.part_id, status, message_id if status == "delivered" else "unconfirmed")
        return status

    async def _poke(self, api, context, plan):
        """Open one message with the sealed native poke, without blocking the text.

        A poke is a separate OneBot action, so it cannot share the message ACK.
        A failure after the API call starts is unknown and never retried. When a
        message accompanies the poke, its independent delivery may still proceed.
        """
        scope = context.scope
        try:
            if scope.kind is ScopeKind.PRIVATE:
                action, target = "friend_poke", {"user_id": int(scope.native_scope_id)}
            elif scope.kind is ScopeKind.GROUP:
                action, target = "group_poke", {"group_id": int(scope.native_scope_id),
                                                "user_id": int(account_number(
                                                    context.actor.principal.native_account_id))}
            else:
                return "not_sent"
            for _ in range(plan.count):
                try:
                    await api.call_action(action, self_id=scope.account_id, **target)
                except Exception as error:
                    logger.warning("V2 poke was not confirmed: %s", type(error).__name__)
                    return "unknown"
            return "delivered"
        except Exception as error:
            logger.warning("V2 poke route was unavailable: %s", type(error).__name__)
            return "not_sent"
            return "not_sent"

    def route(self, context):
        raise NotImplementedError("a sender must bind one explicit audience and API action")


class OneBotPrivateSender(OneBotTextSender):
    supports_images = True

    def route(self, context):
        scope = context.scope
        if (scope.kind is not ScopeKind.PRIVATE or context.audience.recipients != (context.actor.subject,)
                or account_number(context.actor.principal.native_account_id) != scope.native_scope_id):
            raise ValueError("private delivery requires the original single recipient")
        return "send_private_msg", {"user_id": int(scope.native_scope_id)}
