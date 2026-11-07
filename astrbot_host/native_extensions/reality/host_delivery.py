"""Real device output and chat delivery owned by the current V2 runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.text_processing import strip_internal_expression_protocol
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import OperationRef, SourceRef, VersionRef
from companion_v2.sources.models import SourceRecord
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository


class RealityHostDelivery:
    def __init__(self, contexts):
        self.contexts = contexts

    async def send(self, umo, text, *, user_id, operation_key):
        if isinstance(text, str):
            # This path calls the host context directly rather than event.send,
            # so the core presentation boundary may not have seen its text.
            text = strip_internal_expression_protocol(text)
        if not isinstance(text, str) or not text.strip() or not isinstance(operation_key, str) or not operation_key:
            raise TaskError("reality_delivery_invalid", "Reality delivery needs its original operation key and text")
        original = self.contexts.target(user_id, umo=umo)
        await self.contexts.verified_persona(original, user_id, umo=umo)
        application, context = original[1:3]
        token = digest([context.key, operation_key])
        operation = OperationRef("reality-chat-" + token, "reality.chat", context.scope,
            VersionRef(token, 1), token)
        ledger = OperationRepository(application.runtime)
        accepted = require(ledger.reserve(operation, parameter_hash=digest([umo, text]),
                                           observed_at=self.contexts.controller.now()))
        if accepted.replayed:
            # A send that raised or was interrupted remains unknown and is never replayed.
            return accepted.state is OperationState.COMPLETED
        self.contexts.recheck(original, user_id, umo=umo)
        try:
            returned = await self.contexts.controller.context.send_message(umo, MessageChain(chain=[Plain(text)]))
        except BaseException:
            require(ledger.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.UNKNOWN, observed_at=self.contexts.controller.now()))
            raise
        require(ledger.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
            next_state=OperationState.COMPLETED if returned is True else OperationState.UNKNOWN,
            observed_at=self.contexts.controller.now()))
        if returned is True:
            await self.record(user_id, text, source="reality_chat", operation_key=operation_key)
        return returned is True

    async def record(self, user_id, text, *, source="reality_touch_audio", delivered_at=None, operation_key=None):
        original = self.contexts.target(user_id)
        await self.contexts.verified_persona(original, user_id)
        session, application, context, runtime = original
        at = (datetime.fromtimestamp(float(delivered_at), timezone.utc) if delivered_at is not None
              else self.contexts.controller.now())
        identifier = "reality-output-" + digest([context.key, source, operation_key or [at.isoformat(), text]])
        queue = getattr(session, "core_reply_observations", None)
        if queue is None:
            return {"recorded": False, "reason": "reality_history_unavailable"}
        local = source != "reality_chat"

        def observe():
            self.contexts.recheck(original, user_id)
            return queue.owner.capture_reality(context, observation_id=identifier,
                event_id=("reality-local:" if local else "reality-chat:") + identifier,
                text=text, at=at)

        pending = queue.submit(observe)
        if pending is None:
            return {"recorded": False, "reason": "reality_history_unavailable"}
        reference = await asyncio.shield(pending)
        if reference is None:
            return {"recorded": False, "reason": "reality_history_unavailable"}
        self.contexts.recheck(original, user_id)
        result = await runtime.extension_api.record_reality_touch_output(user_id, text,
            source=source, delivered_at=at.timestamp())
        application.notify()
        return {**result, "source_id": reference.source_id, "platform_confirmed": False}

    async def location_candidate(self, user_id, *, diagnostic_origin=None, test_id=None):
        """Build the production location opportunity, optionally for a one-shot diagnostic."""
        from companion_v2.proactive.contact_models import ContactIntent
        from companion_v2.proactive.models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind

        diagnostic = diagnostic_origin is not None
        if diagnostic != (isinstance(test_id, str) and bool(test_id.strip())):
            raise TaskError("proactive_test_confirmation_required", "location diagnostics require their test receipt")
        original = self.contexts.target(user_id)
        _session, application, context, runtime = original
        if not diagnostic:
            application.contact_schedule.bind_context(context)
            application.notify()
        catalog = application.external.catalog_for(context) if application.external is not None else ()
        if "reality.snapshot.read" not in {tool.action for tool in catalog}:
            return {"accepted": False, "reason": "reality_location_not_authorized"}
        if application.proactive.proactive_denied(context):
            return {"accepted": False, "reason": "proactive_contact_not_authorized"}
        observed = runtime.mobile_context(user_id)
        location = observed.get("location") if isinstance(observed.get("location"), dict) else {}
        if not observed.get("available") or not location.get("available"):
            return {"accepted": False, "reason": "reality_location_unavailable"}
        preferences = require(application.proactive.preferences.read(context, observed_at=application.now()))
        if diagnostic:
            preferences = application.contact_schedule._diagnostic_preferences(preferences)
        elif preferences.reason("environment"):
            return {"accepted": False, "reason": preferences.reason("environment")}
        place = location.get("place") if isinstance(location.get("place"), dict) else {}
        label = str((place.get("name") if place.get("matched") else None)
                    or place.get("area_label") or location.get("label") or "").strip()[:120]
        if not label:
            return {"accepted": False, "reason": "reality_location_label_unavailable"}
        fingerprint = digest([label, bool(place.get("matched"))])
        key = "reality.location.last:" + context.key
        prior = application.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
        if not diagnostic and prior is not None and prior["value"] == fingerprint:
            return {"accepted": False, "reason": "reality_location_unchanged"}
        background = await self.contexts.realtime(user_id, purpose="手机前台上报位置变化")
        self.contexts.recheck(original, user_id)
        if "reality.snapshot.read" not in {tool.action for tool in application.external.catalog_for(context)}:
            return {"accepted": False, "reason": "reality_location_not_authorized"}
        current = runtime.mobile_context(user_id)
        current_location = current.get("location") if isinstance(current.get("location"), dict) else {}
        if (not current.get("available") or any(current_location.get(key) != location.get(key)
                for key in ("captured_at", "latitude", "longitude", "place"))):
            return {"accepted": False, "reason": "reality_location_changed"}
        at = application.now()
        reference = SourceRef("reality-location-" + uuid4().hex, "reality.location", context.scope,
            VersionRef("location-" + uuid4().hex, 1), at, at + timedelta(minutes=5))
        payload = encode({"event": "authorized_mobile_place_changed", "place": label,
            "captured_at": location.get("captured_at"), "source": "android_foreground_location",
            "meaning": "手机经授权上报的位置标签，仅供自然关心的参考；不是用户行为、心情或安全状态的证明。"})
        require(application.proactive.sources.publish(SourceRecord(reference, context.audience,
            digest(payload), payload), observed_at=at))
        opportunity = Opportunity("reality-location-" + digest([context.key, reference.source_id]),
            context.scope, at, at + timedelta(minutes=5))
        thought = Thought("reality-location-thought-" + digest(reference), opportunity,
            ThoughtKind.CONVERSATION, "手机陪伴终端本次上报了一条经授权的当前地点标签，判断是否值得自然提起；不把地点变化解释成用户行为、情绪或安全状态，没有合适的话就继续陪伴。",
            EvidenceSnapshot(context.scope, at, (reference,)), 1.0)
        if not diagnostic:
            # 这里只把指纹交给调用方；真正记为"已经用掉这个地点"要等
            # proactive.contact 接受之后（见 location()）。先记后发会让一次
            # 被额度/静默时段挡下的到达永久丢失：下次同一个地点会被当成没变。
            candidate_fingerprint = fingerprint
        else:
            candidate_fingerprint = ""
        candidate = {"accepted": True, "opportunity": opportunity,
            "intents": (ContactIntent("environment", thought),), "role_instruction": background["prompt"],
            "route": {"id": "reality_location_event", "label": "手机陪伴终端地点来源",
                      "variant": "mobile_location"},
            "stages": [{"stage": "mobile_location_fetch", "state": "passed"}]}
        if candidate_fingerprint:
            candidate["place_fingerprint"] = candidate_fingerprint
        if diagnostic:
            candidate["diagnostic_origin"] = {**diagnostic_origin,
                "reality_location_event": {"test_id": test_id,
                    "captured_at": location.get("captured_at"), "source_id": reference.source_id,
                    "place_fingerprint": fingerprint}}
        else:
            candidate["application"] = application
            candidate["context"] = context
        return candidate

    async def location_diagnostic_candidate(self, user_id, *, test_id, diagnostic_origin):
        return await self.location_candidate(user_id, diagnostic_origin=diagnostic_origin, test_id=test_id)

    async def location(self, user_id):
        """Submit a changed, authorized place to V2's ordinary contact worker."""
        candidate = await self.location_candidate(user_id)
        if not candidate.get("accepted"):
            return candidate
        application, context = candidate.pop("application"), candidate.pop("context")
        fingerprint = candidate.pop("place_fingerprint", "")
        result = await application.proactive.contact(context, candidate["opportunity"], candidate["intents"],
            role_instruction=candidate["role_instruction"], selection_seed=candidate["opportunity"].opportunity_id)
        if result.is_success and fingerprint:
            # 只有这里真的把这次到达交给主动联系之后才记指纹：被额度、静默
            # 时段或去重挡下时，下一次同一个地点还要能重新尝试。
            with application.runtime.transaction() as connection:
                connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("reality.location.last:" + context.key, fingerprint))
        return {"accepted": result.is_success,
                "reason": None if result.is_success else result.failure.code}
