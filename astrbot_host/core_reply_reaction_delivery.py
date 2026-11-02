"""Connect model-authored reaction intent to the authorized native sender."""

import asyncio
import base64
import logging

from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.execution.models import TaskError
from companion_v2.conversation.voice_reply import separate_spoken
from companion_v2.expression.reply_intent import (
    INSTRUCTION, authored_reply_decision, extract_reply_intent, reply_context_intent,
)
from .core_reply import CORE_REPLY_CONTEXT_EXTRA


logger = logging.getLogger(__name__)


def prepare_reaction_turn(semantic, context, original, turn_id):
    """Freeze eligibility before AstrBot selects its streaming mode."""
    if semantic is None or original is None:
        return False
    semantic.reaction_preferences.prepare_native(context, turn_id, event_id=original.event_id,
                                                 text=original.text, at=original.at)
    return semantic.reaction_available(context, turn_id)


def _reaction_turn_note(capable, enabled):
    """Tell the model this turn's choice without touching the cached prefix."""
    if not capable:
        return ""
    if enabled:
        return "本轮图库表情可用：需要时调用 reaction.search，或按表达需要附带一次表情意图。"
    return ("本轮不使用图库表情：不要调用 reaction.search，也不要输出表情意图标签；"
            "按普通文字回复，不要向用户解释这条规则。")


def inject_core_reaction_instruction(event, request, semantic, turn_id):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return
    context = core["prepared"]["context"]
    capable = semantic.reaction_capable(context)
    enabled = capable and semantic.reaction_available(context, turn_id)
    previous = core.get("_reaction_reply")
    previous = previous if isinstance(previous, dict) else {}
    core["_reaction_reply"] = previous | {
        "semantic": semantic, "context": context, "turn_id": turn_id, "enabled": enabled,
        # Kept here so an attempted/deferred turn still has a readable reason;
        # the response observer logs it before any capture can run.
        "reason": previous.get("reason", "")}
    if capable and INSTRUCTION not in (request.system_prompt or ""):
        request.system_prompt = ((request.system_prompt or "").rstrip() + "\n\n" + INSTRUCTION).lstrip()
    # The per-turn roll belongs to the current user message, not the system
    # prefix: flipping it there would invalidate the whole cached conversation.
    # The host clears turn context after tool injection, so record it here and
    # let the caller apply it once that pass has finished.
    notes = core.setdefault("_turn_notes", {})
    if isinstance(notes, dict):
        notes["reactions"] = _reaction_turn_note(capable, enabled)


def capture_core_reaction_intent(event, message):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return
    chain = getattr(message, "chain", ())
    text = message if isinstance(message, str) else "".join(part.text for part in chain if isinstance(part, Plain))
    reaction = core.get("_reaction_reply")
    if (isinstance(reaction, dict) and reaction.get("enabled") and not reaction.get("intent")
            and reaction.get("reason") != "model_declined"):
        _capture_reply_intent(core, text)


def _capture_reply_intent(core, text):
    reaction = core.get("_reaction_reply")
    if not isinstance(reaction, dict) or not reaction.get("enabled") or reaction.get("attempted"):
        return
    clean, intent = extract_reply_intent(text)
    decided = authored_reply_decision(text)
    reaction["review_reply"] = not decided
    if not intent and not decided:
        prepared = core["prepared"]
        visible, _ = separate_spoken(clean, prepared.get("voice_reply_policy"))
        original = getattr(prepared.get("native_input"), "text", "")
        intent = reply_context_intent(original, visible)
    reaction["intent"] = intent
    reaction["reason"] = ("intent_authored" if decided and intent else "model_declined" if decided
                          else "reply_context" if intent else "no_reply_text")


def observe_core_reaction_response(event, response):
    """Consume internal intent before native segmentation can discard it."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if (not isinstance(core, dict) or response is None or getattr(response, "is_chunk", False)
            or getattr(response, "role", None) != "assistant"):
        return
    source = getattr(response, "completion_text", "") or ""
    cleaned, intent = extract_reply_intent(source)
    # Strip before any side effect: capture/logging used to abort on a missing
    # reason and leave the protocol in text AstrBot was about to send.
    if cleaned != source:
        response.completion_text = cleaned
        for part in getattr(getattr(response, "result_chain", None), "chain", ()):
            if isinstance(part, Plain):
                part.text = extract_reply_intent(part.text)[0]
    reaction = core.get("_reaction_reply")
    try:
        if (intent and isinstance(reaction, dict) and reaction.get("enabled")
                and not getattr(response, "tools_call_name", ())):
            reaction["intent"] = intent
        if isinstance(reaction, dict) and reaction.get("enabled") and not getattr(response, "tools_call_name", ()):
            _capture_reply_intent(core, source)
            logger.info("[CompanionV2][media] stage=response event_ref=%s media=reaction authored_intent=%s lookup=%s reason=%s",
                        core["prepared"].get("event_ref", "unknown"), bool(intent),
                        bool(reaction.get("intent")), reaction.get("reason", "unset"))
    except Exception:
        logger.debug("[CompanionV2][media] reaction observer degraded", exc_info=True)


async def prepare_core_reaction_message(event, message, *, authorize, segment_index=0, segment_count=1):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    reaction = core.get("_reaction_reply") if isinstance(core, dict) else None
    if not reaction or not reaction.get("enabled") or not reaction.get("intent"):
        return message
    if reaction.get("attempted") or core.get("_reaction_media", {}).get("offers"):
        return message
    semantic, context, turn_id = reaction["semantic"], reaction["context"], reaction["turn_id"]
    try:
        settings = semantic.store.settings()
        segment_index = max(0, int(segment_index))
        segment_count = max(segment_index + 1, int(segment_count))
        mode = settings["reactions_delivery_mode"]
        if ((mode == "separate_after" and segment_index < segment_count - 1)
                or (mode != "separate_after" and segment_index > 0)):
            return message
        chain = getattr(message, "chain", ())
        if any(isinstance(part, Image) for part in chain):
            reaction["attempted"] = True
            reaction["reason"] = "existing_image"
            return message
        reaction["attempted"] = True
        await authorize()
        # Retrieval can include remote embedding, reranking and visual review.
        # A timeout drops the optional attachment and preserves the primary reply.
        kwargs = {"review_reply": True} if reaction.get("review_reply") else {}
        payload = await asyncio.wait_for(semantic.reactions.search_native(
            context, turn_id, "reply-attachment", reaction["intent"], **kwargs),
            timeout=settings["timeout_seconds"] * 3)
        await authorize()
        images = semantic.reactions.native_images(context, turn_id, "reply-attachment", payload)
        if not images:
            reaction["reason"] = "no_matching_image"
            logger.info("[CompanionV2] 表情附件未命中 event_ref=%s reason=%s",
                        core["prepared"].get("event_ref", "unknown"), reaction["reason"])
            return message
        original = images[0]
        image = Image.fromBase64(base64.b64encode(original.content).decode("ascii"))
        object.__setattr__(image, "_companion_v2_reaction_attachment", reaction)
        reaction.update(payload=payload, content_hash=original.descriptor.content_hash,
                        delivery_mode=settings["reactions_delivery_mode"], image_format=settings["reactions_image_format"],
                        reason="prepared", dispatch_state="pending")
        review = payload["items"][0].get("review", {})
        logger.info("[CompanionV2][media] stage=retrieval event_ref=%s media=reaction state=prepared review=%s review_code=%s",
                    core["prepared"].get("event_ref", "unknown"), review.get("state", "unknown"),
                    review.get("code", "none"))
        components = list(chain) if chain else [Plain(message)] if isinstance(message, str) else []
        if settings["reactions_delivery_mode"] == "separate_before":
            components.insert(0, image)
        else:
            components.append(image)
        derive = getattr(message, "derive", None)
        output = derive(components) if callable(derive) else MessageChain(chain=components)
        source = getattr(message, "_companion_v2_voice_source", None)
        if source is not None:
            object.__setattr__(output, "_companion_v2_voice_source", source)
        return output
    except TaskError as error:
        if core.get("denial_notified"):
            raise
        reaction["reason"] = error.code
    except Exception as error:
        reaction["reason"] = type(error).__name__
    logger.warning("[CompanionV2] 表情附件退回正文 event_ref=%s reason=%s",
                   core["prepared"].get("event_ref", "unknown"), reaction["reason"])
    return message


def observe_core_reaction_returned(event, message, *, submit):
    """Count usage only for an original included in a returned platform send."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    reaction = core.get("_reaction_reply") if isinstance(core, dict) else None
    if not reaction or reaction.get("dispatch_state") == "returned":
        return
    if not any(getattr(part, "_companion_v2_reaction_attachment", None) is reaction
               for part in getattr(message, "chain", ())):
        return
    reaction["dispatch_state"] = "returned"
    semantic = reaction["semantic"]
    observed_at = semantic.app.now()
    if callable(submit):
        submit(lambda: semantic.reactions.mark_reply_returned(
            reaction["context"], reaction["turn_id"], "reply-attachment", reaction["payload"],
            reaction["content_hash"], observed_at))
    logger.info("[CompanionV2] 表情附件发送返回 event_ref=%s",
                core["prepared"].get("event_ref", "unknown"))
