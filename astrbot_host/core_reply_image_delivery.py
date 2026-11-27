"""Track delivery of the native generated images offered to this turn."""

import logging
from pathlib import Path

from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.media.output_settings import is_rule_image_request
from companion_v2.foundation.refs import ScopeKind
from .core_reply import CORE_REPLY_CONTEXT_EXTRA


logger = logging.getLogger(__name__)


def offer_generated_images(session, tool_name, call_id, artifact_ids):
    if session.context.scope.kind is not ScopeKind.PRIVATE:
        return
    original = session.prepared.get("native_input")
    command = session.state.get("functional_command")
    if (getattr(command, "intent", None) not in {"image_generate", "image_edit"}
            and not is_rule_image_request(getattr(original, "text", ""),
                                          has_images=bool(getattr(original, "images", ())))):
        return
    delivery = session.state.setdefault("_generated_image_delivery", {"offers": {}, "cached": {}})
    delivery["offers"][(tool_name, call_id)] = frozenset(artifact_ids)


def observe_generated_image_cache(event, cached_image):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    delivery = core.get("_generated_image_delivery") if isinstance(core, dict) else None
    if delivery is None:
        return
    key = (cached_image.tool_name, cached_image.tool_call_id)
    if key not in delivery["offers"]:
        return
    path = Path(cached_image.file_path).resolve()
    delivery["cached"].setdefault((str(path), path.as_uri()), "pending")


def observe_generated_image_publication(session, arguments):
    delivery = session.state.get("_generated_image_delivery")
    if delivery is not None:
        published = set(arguments.get("image_ids") or ())
        if any(published.intersection(artifacts) for artifacts in delivery["offers"].values()):
            delivery["publication_attempted"] = True


def observe_generated_image_send(delivery, message, outcome):
    if delivery is None:
        return
    locations = {location for part in message.chain if isinstance(part, Image)
                 for location in (part.path, part.file) if location}
    for locators, previous in delivery["cached"].items():
        if previous != "returned" and locations.intersection(locators):
            delivery["cached"][locators] = outcome


async def complete_generated_image_delivery(event, response, *, runner):
    """Keep a missed image send visible without treating an uncertain send as replayable."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    delivery = core.get("_generated_image_delivery") if isinstance(core, dict) else None
    if (not delivery or delivery.get("reported") or delivery.get("publication_attempted")
            or getattr(response, "role", None) != "assistant" or getattr(response, "tools_call_name", ())
            or event.is_stopped() or getattr(runner, "was_aborted", lambda: False)()):
        return
    result = getattr(response, "result_chain", None)
    if any(isinstance(part, Image) for part in getattr(result, "chain", ())):
        return  # The normal reply path will deliver the already attached image.
    outcomes = set(delivery["cached"].values())
    if "returned" in outcomes:
        return
    if "unknown" in outcomes or "started" in outcomes:
        status, notice = "unknown", "图片已生成，发送结果暂时无法确认。"
    elif "failed" in outcomes:
        status, notice = "failed", "图片已生成，但平台发送失败。"
    elif outcomes:
        status, notice = "not_sent", "图片已生成，但这轮没有发送。"
    else:
        status, notice = "cache_unavailable", "图片已生成，但宿主没有返回可投递的图片。"
    delivery["reported"] = True
    logger.info("[CompanionV2][image] stage=delivery event_ref=%s state=%s",
                core["prepared"].get("event_ref", "unknown"), status)
    if runner is not None and runner.streaming:
        # A native stream can already be showing the caption. Send only the notice.
        try:
            await event.send(MessageChain().message(notice))
        except Exception as error:
            logger.warning("[CompanionV2] 图片投递状态提醒发送失败 error_type=%s", type(error).__name__)
    elif result is not None:
        result.chain.append(Plain(text=("\n\n" if response.completion_text else "") + notice))
    else:
        text = response.completion_text or ""
        response.completion_text = "\n\n".join(part for part in (text, notice) if part)
