"""Capture native image references in background or on an explicit tool need."""

import asyncio
from dataclasses import dataclass, field, replace
import json

from astrbot.core.message.components import Image, Reply
from astrbot.core.utils.quoted_message.chain_parser import OneBotPayloadParser
from astrbot.core.utils.quoted_message.image_resolver import ImageResolver
from astrbot.core.utils.quoted_message.onebot_client import OneBotClient

from companion_v2.adapters.astrbot.history import extract_history_messages
from companion_v2.adapters.astrbot.native import NativeImageLocator
from companion_v2.adapters.astrbot.quotes import image_locator
from companion_v2.conversation.media.models import ImageConversationEvent, MAX_INPUT_IMAGES
from companion_v2.conversation.native_tool_sources import native_input_images
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord

from .media import load_native_image, public_url


QUOTED_IMAGE_OWNER = "conversation.native_quoted_attachments"


def _identifier(value):
    if type(value) not in (str, int):
        return None
    value = str(value).strip()
    return value if value and value != "0" and len(value) <= 1024 else None


@dataclass(frozen=True)
class _QuotedImages:
    position: int
    message_id: str
    sender_id: str | None
    sent_at: int | None
    images: tuple
    event: object = field(repr=False, compare=False)
    client: object = field(repr=False, compare=False)

    @property
    def needs_lookup(self):
        return not self.images or self.sender_id is None or self.needs_resolved_images

    @property
    def needs_resolved_images(self):
        for image in self.images:
            if image.locator.startswith("base64://"):
                continue
            try:
                public_url(image.locator)
            except TaskError:
                return True
        return False


def direct_image_locators(locators):
    """Keep the direct attachments and drop every quoted reference.

    ``frozen_image_locators`` freezes attachments and quotes in one sequence so
    that explicit image tools keep AstrBot's component order.  Passive reaction
    collection must never read a quote: the model may not have seen that image
    at all, and a quote that still needs a lookup would otherwise take the
    sender's own attachment down with it.
    """
    return tuple(item for item in locators if not isinstance(item, _QuotedImages))


def frozen_image_locators(event):
    """Freeze current attachments and quotes, in AstrBot's attachment order."""
    images, quotes = [], []
    for position, part in enumerate(getattr(getattr(event, "message_obj", None), "message", ())):
        if type(part) is Image:
            images.append(image_locator(position, part.file, part.url))
        elif type(part) is Reply:
            identifier = _identifier(part.id)
            if identifier is None:
                continue
            embedded = tuple(image_locator(index, item.file, item.url)
                             for index, item in enumerate(part.chain or ()) if type(item) is Image)
            quotes.append(_QuotedImages(position, identifier, _identifier(part.sender_id),
                part.time if type(part.time) is int and part.time > 0 else None,
                embedded, event, OneBotClient(event)))
    return (*images, *quotes)


def _quote_sender(context, sender):
    if (sender is None or context.scope.kind is ScopeKind.PRIVATE
            and sender not in (context.scope.native_scope_id, context.scope.account_id)):
        raise TaskError("host_quote_scope_mismatch", "quoted sender does not belong to this conversation")


async def _quoted_images(quote, context, check):
    try:
        return await _resolve_quoted_images(quote, context, check)
    except TaskError:
        raise
    except asyncio.TimeoutError:
        raise TaskError("host_quote_timeout", "quoted image lookup did not complete in time") from None
    except Exception:
        raise TaskError("host_quote_unavailable", "original quoted images could not be verified") from None


async def _resolve_quoted_images(quote, context, check):
    sender, sent_at, images = quote.sender_id, quote.sent_at, quote.images
    resolve_from_original = not images or quote.needs_resolved_images
    if quote.needs_lookup:
        await check()
        if OneBotClient._resolve_call_action(quote.event) != quote.client._call_action:
            raise TaskError("host_quote_account_changed", "the original quote connection changed")
        response = await asyncio.wait_for(quote.client.get_msg(quote.message_id), timeout=10)
        raw = response.get("data", response) if isinstance(response, dict) else None
        if not isinstance(raw, dict):
            raise TaskError("host_quote_unavailable", "the referenced message could not be read")
        sender = _identifier(raw.get("sender", {}).get("user_id"))
        _quote_sender(context, sender)
        if context.scope.kind is ScopeKind.PRIVATE and sender == context.scope.account_id:
            # Outgoing get_msg.user_id identifies the bot, not the recipient.
            # Confirm this exact message in the original peer's scoped history.
            history = await asyncio.wait_for(quote.client.call("get_friend_msg_history", {
                "self_id": context.scope.account_id, "user_id": context.scope.native_scope_id,
                "message_seq": quote.message_id, "count": 1, "reverse_order": False,
                "parse_mult_msg": True}), timeout=10)
            records = extract_history_messages(history)
            if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                raise TaskError("host_quote_scope_mismatch", "the outgoing quote has no original peer binding")
            raw = records[0]
        expected_kind = context.scope.kind.value
        if (_identifier(raw.get("message_id")) != quote.message_id
                or raw.get("message_type") != expected_kind
                or raw.get("self_id") is not None and _identifier(raw["self_id"]) != context.scope.account_id
                or expected_kind == "group" and _identifier(raw.get("group_id")) != context.scope.native_scope_id
                or expected_kind == "private" and raw.get("group_id") not in (None, "", 0, "0")
                or raw.get("anonymous") is not None or raw.get("sub_type") == "anonymous"
                or _identifier(raw.get("sender", {}).get("user_id")) != sender
                or raw.get("user_id") is not None and _identifier(raw["user_id"]) != sender
                or quote.sender_id is not None and quote.sender_id != sender):
            raise TaskError("host_quote_scope_mismatch", "quote is not the requested message in the original route")
        if OneBotClient._resolve_call_action(quote.event) != quote.client._call_action:
            raise TaskError("host_quote_account_changed", "the original quote connection changed")
        await check()
        references = OneBotPayloadParser().parse_get_msg_payload(raw)["image_refs"]
        if len(references) > MAX_INPUT_IMAGES:
            raise TaskError("host_image_limit", "quoted images exceed the image intake budget")
        if resolve_from_original:
            references = await ImageResolver(quote.event, quote.client).resolve_for_llm(references)
            images = tuple(NativeImageLocator(index, value, host_resolved=True)
                           for index, value in enumerate(references))
        sent_at = raw.get("time") if type(raw.get("time")) is int and raw["time"] > 0 else sent_at
    _quote_sender(context, sender)
    return images, {"origin": "quoted_message", "original_message_id": quote.message_id,
                    "original_sender": sender, "sent_at_unix": sent_at, "component_position": quote.position}


def bind_native_quoted_image_sources(application):
    """Keep stored quote attribution and withdrawals valid at every source read."""
    guard = getattr(application, "_native_quoted_image_guard", None)
    if guard is not None and not guard.closed:
        return guard

    def validate(record, at):
        document = json.loads(record.payload)
        context = TaskContext.restore(document["context"])
        if (record.reference.owner != QUOTED_IMAGE_OWNER or record.reference.scope != context.scope
                or record.audience != context.audience or record.content_hash != digest(record.payload)
                or record.reference.source_id != "core-attachments-" + digest([context.key, document["event_id"]])
                or len(document["images"]) != len(document["image_sources"])):
            raise TaskError("native_image_context_changed", "the original native quote attribution changed")
        require(application.tasks.check_context_access(context, action="conversation.reply", observed_at=at))
        application.messages.recalls.check(context.scope, document["event_id"], context.actor.principal.native_account_id)
        for source in document["image_sources"]:
            if source["origin"] == "quoted_message":
                _quote_sender(context, source["original_sender"])
                application.messages.recalls.check(context.scope, source["original_message_id"], source["original_sender"])

    guard = bind(application.runtime, QUOTED_IMAGE_OWNER, validate)
    application._native_quoted_image_guard = guard
    return guard


async def prepare_native_input_images(session, wrapper):
    if not session.image_locators:
        return
    async def authorize():
        await session.authorize(wrapper)
    await _capture_native_images(session.application, session.prepared, session.image_locators,
                                 turn_id=session.turn_id, authorize_async=authorize)


async def capture_native_input_images(application, prepared, image_locators, *, authorize):
    """Capture an admitted input asynchronously, including all SQL and checks."""
    # Reply-id lookup belongs to the model's explicit image tool need. Passive
    # collection must not duplicate core get_msg parsing or publish half a turn.
    # A quote that still needs a lookup only disqualifies itself: the sender's
    # own attachments are already frozen and stay collectable on their own.
    locators = direct_image_locators(image_locators)
    if not locators:
        return None
    return await _capture_native_images(application, prepared, locators,
        turn_id=prepared.get("turn_id") or "core-reply:" + digest([prepared["context"].key, prepared["event_id"]]),
        authorize_sync=authorize)


async def _capture_native_images(app, prepared, locators, *, turn_id, authorize_sync=None, authorize_async=None):
    if not locators:
        return None
    context, original = prepared["context"], prepared.get("native_input")
    if (original is None or original.principal != context.actor.principal or original.route.scope != context.scope
            or original.role != context.role_binding.role or original.recipients != context.audience):
        raise TaskError("native_image_context_changed", "native images require their original verified input")

    async def check():
        if authorize_async is not None:
            await authorize_async()
        if authorize_sync is not None:
            await asyncio.to_thread(authorize_sync)

    bind_native_quoted_image_sources(app)
    await check()
    previous = await asyncio.to_thread(native_input_images, app.messages, context, turn_id, optional=True)
    if previous is not None:
        await asyncio.to_thread(app.messages._read_source, previous.source, context, previous.capability, app.tasks.now())
        return previous
    images, provenance = [], []
    for locator in locators:
        if isinstance(locator, _QuotedImages):
            if locator.message_id == original.event_id:
                raise TaskError("quote_original_cycle", "an input cannot quote itself")
            references, attribution = await _quoted_images(locator, context, check)
            app.messages.recalls.check(context.scope, attribution["original_message_id"], attribution["original_sender"])
        else:
            references, attribution = (locator,), {"origin": "attachment", "component_position": locator.position}
        if len(images) + len(references) > MAX_INPUT_IMAGES:
            raise TaskError("host_image_limit", "native attachments exceed the image intake budget")
        for item in references:
            images.append(await load_native_image(replace(item, position=len(images))))
            provenance.append({**attribution, "quoted_by": original.principal.native_account_id,
                               "position": len(provenance)})
    if not images:
        return None
    images = tuple(images)
    await check()
    identifier = "core-attachments-" + digest([context.key, original.event_id])
    quoted = any(source["origin"] == "quoted_message" for source in provenance)
    reference = SourceRef(identifier, QUOTED_IMAGE_OWNER if quoted else "conversation.native_attachments", context.scope,
                          VersionRef(identifier, 1), original.at)
    payload = encode({"kind": "native_image_input", "text": original.text,
                      "images": [image.descriptor for image in images], "image_sources": provenance,
                      "event_id": original.event_id, "context": context})
    event = ImageConversationEvent(original.event_id, original.principal, original.route.scope, reference,
        "text", digest(payload), original.at, images=tuple(image.descriptor for image in images))
    document = {"context_key": context.key, "event_json": encode(event), "event_hash": digest(event),
                "turn_id": turn_id}

    def publish():
        if authorize_sync is not None:
            authorize_sync()
        with app.runtime.transaction() as connection:
            previous = native_input_images(app.messages, context, turn_id, optional=True)
            if previous is not None:
                return previous
            app.messages.recalls.check(context.scope, original.event_id, original.principal.native_account_id)
            require(app.messages.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload),
                                                 observed_at=app.tasks.now()))
            app.messages.images.publish(context, event, images)
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                               ("astrbot.core-input-images." + digest(turn_id), encode(document)))
        return native_input_images(app.messages, context, turn_id)

    return await asyncio.to_thread(publish)
