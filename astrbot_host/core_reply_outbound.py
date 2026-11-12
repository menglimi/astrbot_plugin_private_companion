"""Apply captured V2 group reply decorations at AstrBot's send boundary."""

from collections.abc import Mapping
import logging

from astrbot.core.message.components import At, AtAll, Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.conversation.outbound_plan import outbound_plan
from companion_v2.delivery.components import order_rank
from companion_v2.execution.models import TaskError
from .core_reply import sent_core_text
from .core_reply_presentation import CONTROL_TYPES


logger = logging.getLogger(__name__)


def _kind(component):
    if isinstance(component, Record):
        return "voice"
    if isinstance(component, (At, AtAll)):
        return "at"
    if isinstance(component, Plain):
        return "text"
    if isinstance(component, Image):
        return "image"
    if type(component).__name__ == "Face":
        return "face"
    return None


def _trusted_inputs(prepared):
    captured = prepared.get("native_input")
    if (not isinstance(captured, NativeConversationInput)
            or captured.event_id != prepared.get("event_id")):
        return ()
    principal = captured.principal
    account = getattr(principal, "native_account_id", None)
    return ((captured.event_id, account),)


def _derive(message, chain):
    derive = getattr(message, "derive", None)
    return derive(chain) if callable(derive) else MessageChain(chain=chain)


def prepare_core_reply_outbound_message(event, prepared, message, *, segment_index=0):
    """Attach the sealed quote/@ plan and honor explicit component order."""
    if (not isinstance(prepared, Mapping) or getattr(message, "type", None) in CONTROL_TYPES):
        return message
    settings = prepared.get("outbound_settings")
    if not isinstance(settings, Mapping):
        return message
    chain = getattr(message, "chain", None)
    if isinstance(message, str):
        chain = [Plain(message)]
    elif not isinstance(chain, (list, tuple)):
        return message

    text = message if isinstance(message, str) else sent_core_text(message)
    try:
        core = event.get_extra("_companion_v2_core_reply_context")
        first_text_segment = core.get("outbound_first_text_segment", 0) if isinstance(core, dict) else 0
        from .core_reply_behavior import behavior_targets
        behavior_mentions = behavior_targets(event, prepared,
            first_text=segment_index == first_text_segment, text=text)
        if isinstance(core, dict) and core.get("_behavior_plan"):
            # A directed interaction answers its selected member. Do not also
            # quote or ping the unrelated member who happened to open the turn.
            settings = {**settings, "reply_quote_enabled": False, "reply_mention_speaker_enabled": False}
        native_ids = getattr(getattr(event, "platform_meta", None), "name", "") == "wechat_native"
        # The persisted OutboundPlan is a OneBot-number contract. WeChat's
        # original event owns opaque IDs; add its headers only at this native
        # send boundary instead of changing the shared storage contract.
        plan_settings = ({**settings, "reply_quote_enabled": False, "reply_mention_speaker_enabled": False}
                         if native_ids else settings)
        plan = outbound_plan(
            settings=plan_settings,
            scope_kind=prepared.get("channel", "private"),
            sequence=max(1, int(segment_index) + 1),
            first_text_sequence=max(1, int(first_text_segment) + 1),
            text=text,
            inputs=_trusted_inputs(prepared),
        )
    except TaskError:
        raise
    except (TypeError, ValueError):
        logger.warning("[CompanionV2] 核心回复出站设置无效，保留宿主消息")
        return message
    native_quote, native_mentions = "", ()
    inputs = _trusted_inputs(prepared)
    if (native_ids and inputs and prepared.get("channel") == "group" and text.strip()
            and (segment_index == first_text_segment or not settings.get("reply_quote_first_part_only", True))):
        threshold = settings.get("reply_quote_skip_chars", 0)
        if (settings.get("reply_quote_enabled", False) and settings.get("reply_quote_group_enabled", True)
                and (not threshold or len("".join(text.split())) > threshold)):
            native_quote = inputs[0][0]
        if settings.get("reply_mention_speaker_enabled", False):
            native_mentions = (inputs[0][1],)
    quote_id = native_quote or (plan.quote if plan else "")
    output = list(chain)
    quote = bool(quote_id) and not any(isinstance(component, Reply) for component in output)
    mentions = [account for account in (behavior_mentions or native_mentions or (plan.mentions if plan else ()))
                if not any(isinstance(component, At)
                           and str(getattr(component, "qq", "")) == account for component in output)]
    mention_components = [At(qq=account) for account in mentions]
    order = plan.order if plan else ()
    if order:
        output.extend(mention_components)
        ordered_kinds = [_kind(component) for component in output]
        if all(kind is not None for kind in ordered_kinds):
            rank = order_rank(order)
            output.sort(key=lambda component: rank[_kind(component)])
    elif mention_components:
        first_text = next((index for index, component in enumerate(output)
                           if isinstance(component, Plain)), None)
        insert_at = first_text if first_text is not None else 0
        output[insert_at:insert_at] = mention_components

    if not order:
        images = [component for component in output if isinstance(component, Image)]
        if images and any(isinstance(component, Plain) for component in output):
            output = [component for component in output if not isinstance(component, Image)]
            if settings.get("image_position", "after_text") == "before_text":
                insert_at = next(index for index, component in enumerate(output)
                                 if isinstance(component, Plain))
            else:
                insert_at = max(index for index, component in enumerate(output)
                                if isinstance(component, Plain)) + 1
            output[insert_at:insert_at] = images

    if quote:
        output.insert(0, Reply(id=quote_id))
    if output == list(chain):
        return message
    return _derive(message, output)
