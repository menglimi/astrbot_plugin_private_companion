"""Capture native image references in background or on an explicit tool need."""

import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from astrbot.core.message.components import Image, Reply
from astrbot.core.utils.quoted_message.chain_parser import OneBotPayloadParser
from astrbot.core.utils.quoted_message.image_resolver import ImageResolver
from astrbot.core.utils.quoted_message.onebot_client import OneBotClient

from companion_v2.adapters.astrbot.history import fetch_onebot_history_record, parse_signed_int
from companion_v2.adapters.astrbot.native import NativeImageLocator, event_account_id, event_platform_name
from companion_v2.adapters.astrbot.quotes import image_locator
from companion_v2.conversation.media.generated import IMAGE_ACTIONS
from companion_v2.conversation.media.models import CapturedImage, ImageConversationEvent, MAX_INPUT_IMAGES
from companion_v2.conversation.native_tool_sources import native_input_images
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.governance import bind
from companion_v2.sources.models import GovernanceState, SourceRecord

from .media import inspect_image, load_native_image, public_url


logger = logging.getLogger(__name__)
QUOTED_IMAGE_OWNER = "conversation.native_quoted_attachments"
_BARE_MD5_IMAGE_RE = re.compile(
    r"^\{?([0-9a-fA-F]{8})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{12})\}?"
    r"(?:\.[a-zA-Z0-9]{1,8})?$"
)
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_LOCAL_MD5_SEARCH_LIMIT = 32


@dataclass(frozen=True)
class _LocalRecoveredImage:
    position: int
    image: CapturedImage


def extract_bare_image_md5(value) -> str | None:
    """Extract a 32-char lowercase hex MD5 from a bare OneBot image filename.

    Strictly rejects paths, URLs, and arbitrary strings so only unresolved
    protocol filenames (e.g. ``fb3f...d1.png`` or ``{UUID}.jpg``) qualify.
    """
    if type(value) is not str:
        return None
    stripped = value.strip()
    if not stripped or "/" in stripped or "\\" in stripped or ":" in stripped:
        return None
    match = _BARE_MD5_IMAGE_RE.fullmatch(stripped)
    if match is None:
        return None
    return "".join(match.groups()).lower()


def _find_native_generated_image_by_md5(application, context, target_md5: str, at):
    runtime = getattr(application, "runtime", None)
    messages = getattr(application, "messages", None)
    external = getattr(getattr(application, "tools", None), "external", None)
    if runtime is None or messages is None or external is None:
        return None
    from companion_v2.conversation.external_tools import _SourceAccess
    from companion_v2.conversation.messages import source_from_document
    from companion_v2.conversation.native_tool_media import read_native_images
    from companion_v2.conversation.native_tool_results import PREFIX, PREFIX_END, operation_row

    actions = tuple(sorted(IMAGE_ACTIONS))
    try:
        rows = runtime.read_all(
            """SELECT json_extract(m.value, '$.invocation_id') AS invocation_id
               FROM runtime_metadata m
               JOIN execution_tasks t ON t.task_id = json_extract(m.value, '$.task_id')
               WHERE m.key >= ? AND m.key < ?
                 AND json_extract(m.value, '$.origin_key') = ?
                 AND json_extract(m.value, '$.action') IN (?,?,?)
                 AND json_extract(m.value, '$.state') = 'succeeded'
                 AND t.state = 'succeeded'
               ORDER BY t.created_at DESC, invocation_id DESC
               LIMIT ?""",
            (PREFIX, PREFIX_END, context.key, *actions, _LOCAL_MD5_SEARCH_LIMIT),
        )
    except Exception:
        return None
    for selected in rows:
        invocation_id = selected["invocation_id"]
        if not isinstance(invocation_id, str) or not invocation_id:
            continue
        try:
            row = operation_row(runtime, invocation_id)
            if (row is None or row.get("origin_key") != context.key
                    or row.get("state") != "succeeded"
                    or row.get("action") not in IMAGE_ACTIONS
                    or not row.get("images") or not row.get("result_source_json")):
                continue
            provider = external.providers.get(row["action"])
            if provider is None:
                continue
            parent = source_from_document(json.loads(row["result_source_json"]))
            if parent.scope != context.scope:
                continue
            capability = provider.descriptor.binding.capability
            authorizer = _SourceAccess(external, provider.descriptor, context)
            parent_source = require(messages.sources.read(
                parent, resolved=context.actor, capability=capability,
                observed_at=at, authorizer=authorizer))
            if (parent_source.state is not GovernanceState.ACTIVE
                    or parent_source.audience != context.audience
                    or digest(parent_source) != row.get("result_hash")):
                continue
            captured_images = read_native_images(
                messages, context, parent, row["action"], row["images"],
                capability=capability, authorizer=authorizer, at=at)
            for captured in captured_images:
                if hashlib.md5(captured.content).hexdigest().lower() == target_md5:
                    return captured
        except Exception:
            continue
    return None


def _find_batch_generated_image_by_md5(application, context, target_md5: str, at):
    runtime = getattr(application, "runtime", None)
    messages = getattr(application, "messages", None)
    generated_images = getattr(messages, "generated_images", None)
    if runtime is None or generated_images is None:
        return None
    import base64

    actions = tuple(sorted(IMAGE_ACTIONS))
    try:
        rows = runtime.read_all(
            """SELECT i.*
               FROM extension_tool_invocations i
               JOIN conversation_batches b ON b.batch_id = i.batch_id
               WHERE b.origin_key = ? AND b.state = 'ready'
                 AND i.action IN (?,?,?) AND i.state = 'succeeded'
               ORDER BY i.created_at DESC, i.invocation_id DESC
               LIMIT ?""",
            (context.key, *actions, _LOCAL_MD5_SEARCH_LIMIT),
        )
    except Exception:
        return None
    for row in rows:
        try:
            if generated_images.rejected(row) is not None:
                continue
            batch_images = generated_images.read_batch(row["batch_id"], context, at)
            parent = generated_images.reference(row, context)
            for item in batch_images:
                if (item.parent.source_id != parent.source_id
                        or item.source.scope != context.scope
                        or item.parent.scope != context.scope):
                    continue
                raw = base64.b64decode(item.data_base64, validate=True)
                if hashlib.md5(raw).hexdigest().lower() == target_md5:
                    return CapturedImage(item.descriptor, raw)
        except Exception:
            continue
    return None


def find_session_generated_image_by_md5(application, context, target_md5: str) -> CapturedImage | None:
    """Find a governed generated image in the exact same context/scope/audience by MD5."""
    if application is None or not isinstance(context, TaskContext):
        return None
    if not isinstance(target_md5, str) or len(target_md5) != 32:
        return None
    normalized = target_md5.lower()
    tasks = getattr(application, "tasks", None)
    at = tasks.now() if tasks is not None and callable(getattr(tasks, "now", None)) else None
    if at is None:
        return None
    matched = _find_native_generated_image_by_md5(application, context, normalized, at)
    if matched is not None:
        return matched
    return _find_batch_generated_image_by_md5(application, context, normalized, at)


def _materialize_temp_quoted_image(captured: CapturedImage) -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
        root = Path(get_astrbot_temp_path()) / "companion_quoted_recovery"
    except Exception:
        root = Path(tempfile.gettempdir()) / "companion_quoted_recovery"
    root.mkdir(parents=True, exist_ok=True)
    suffix = _MIME_EXTENSIONS.get(captured.descriptor.mime_type, ".png")
    path = root / f"quoted_{captured.descriptor.content_hash[:16]}_{uuid4().hex[:8]}{suffix}"
    path.write_bytes(captured.content)
    return path


def _image_component_needs_recovery(component) -> str | None:
    """Return bare MD5 when an embedded Reply Image has no resolvable URL or local file."""
    url = getattr(component, "url", None) or ""
    file_ref = getattr(component, "file", None) or ""
    for candidate in (url, file_ref):
        if type(candidate) is not str or not candidate.strip():
            continue
        stripped = candidate.strip()
        if stripped.startswith(("http://", "https://", "base64://", "data:", "file://")):
            return None
        try:
            if Path(stripped).is_file():
                return None
        except OSError:
            pass
    return extract_bare_image_md5(url) or extract_bare_image_md5(file_ref)


async def repair_quoted_bot_generated_images(event, prepared, *, application) -> int:
    """Materialize unresolved bare-MD5 bot-sent quoted images for the core LLM.

    Runs right before AstrBot's main agent reads ``Reply.chain`` so quoted
    images generated in this exact session no longer degrade into
    ``[Image unavailable]`` when the OneBot backend stores empty image URLs on
    self-sent messages.  Strictly requires:
      1. ``Reply.sender_id == context.scope.account_id`` (bot-sent only),
      2. non-self-quoting message ID not withdrawn/recalled, and
      3. exact 128-bit MD5 byte match against a governed generated image from
         this same ``context.key`` / ``context.scope`` / ``context.audience``.
    """
    if application is None or not isinstance(prepared, dict):
        return 0
    context = prepared.get("context")
    original = prepared.get("native_input")
    if not isinstance(context, TaskContext) or original is None:
        return 0
    parts = getattr(getattr(event, "message_obj", None), "message", None)
    if not parts:
        return 0
    repaired = 0
    for part in parts:
        if type(part) is not Reply or not part.chain:
            continue
        reply_id = _identifier(part.id)
        reply_sender = _identifier(part.sender_id)
        if (reply_id is None or reply_id == original.event_id
                or reply_sender != context.scope.account_id):
            continue
        pending = []
        for item in part.chain:
            if type(item) is Image:
                md5_hex = _image_component_needs_recovery(item)
                if md5_hex is not None:
                    pending.append((item, md5_hex))
        if not pending:
            continue
        try:
            application.messages.recalls.check(context.scope, reply_id, reply_sender)
        except TaskError:
            continue
        for item, md5_hex in pending:
            captured = await asyncio.to_thread(find_session_generated_image_by_md5, application, context, md5_hex)
            if captured is None:
                continue
            temp_path = await asyncio.to_thread(_materialize_temp_quoted_image, captured)
            path_str = str(temp_path)
            tracker = getattr(event, "track_temporary_local_file", None)
            if callable(tracker):
                tracker(path_str)
            item.file = path_str
            item.path = path_str
            item.url = path_str
            repaired += 1
    if repaired:
        logger.info("[CompanionV2] 已按本地 MD5 补齐自发引用图用于本轮视觉上下文 count=%d", repaired)
    return repaired


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
        # Telegram delivers the quoted message itself; there is no lookup to make.
        return self.client is not None and (
            not self.images or self.sender_id is None or self.needs_resolved_images)

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
            sender, client = _identifier(part.sender_id), None
            if event_platform_name(event) != "telegram":
                client = OneBotClient(event)
            elif not embedded:
                continue
            elif sender == event.client.token.partition(":")[0]:
                # The route names this bot by its platform instance, not by its public Telegram ID.
                sender = event_account_id(event)
            # AstrBot's Telegram quote carries the receive time, not when the original was sent.
            quotes.append(_QuotedImages(position, identifier, sender,
                part.time if client is not None and type(part.time) is int and part.time > 0 else None,
                embedded, event, client))
    return (*images, *quotes)


def _quote_sender(context, sender):
    if (sender is None or context.scope.kind is ScopeKind.PRIVATE
            and sender not in (context.scope.native_scope_id, context.scope.account_id)):
        raise TaskError("host_quote_scope_mismatch", "quoted sender does not belong to this conversation")


def _raw_message_image_md5s(raw) -> tuple[str | None, ...]:
    """Return per-image-segment bare MD5s from a verified OneBot message payload."""
    if not isinstance(raw, dict):
        return ()
    parts = raw.get("message")
    if not isinstance(parts, list):
        return ()
    result = []
    for seg in parts:
        if not isinstance(seg, dict) or seg.get("type") != "image":
            continue
        data = seg.get("data")
        if not isinstance(data, dict):
            continue
        md5_hex = extract_bare_image_md5(data.get("file")) or extract_bare_image_md5(data.get("file_id"))
        result.append(md5_hex)
    return tuple(result)


async def _quoted_images(quote, context, check, *, app=None):
    try:
        return await _resolve_quoted_images(quote, context, check, app=app)
    except TaskError:
        raise
    except asyncio.TimeoutError:
        raise TaskError("host_quote_timeout", "quoted image lookup did not complete in time") from None
    except Exception:
        raise TaskError("host_quote_unavailable", "original quoted images could not be verified") from None


async def _resolve_quoted_images(quote, context, check, *, app=None):
    sender, sent_at, images = quote.sender_id, quote.sent_at, quote.images
    resolve_from_original = not images or quote.needs_resolved_images
    fallback_md5s: tuple[str | None, ...] = ()
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
            # Confirm this exact message in the original peer's scoped history,
            # supporting both NapCat (message_seq) and SnowLuma (message_id).
            peer_int = parse_signed_int(context.scope.native_scope_id)
            msg_int = parse_signed_int(quote.message_id)
            primary_params = {
                "self_id": context.scope.account_id,
                "user_id": context.scope.native_scope_id,
                "message_seq": quote.message_id,
                "count": 1,
                "reverse_order": False,
                "parse_mult_msg": True,
            }
            fallback_params = {
                "self_id": context.scope.account_id,
                "user_id": peer_int if peer_int is not None else context.scope.native_scope_id,
                "message_id": msg_int if msg_int is not None else quote.message_id,
                "count": 1,
                "reverse_order": False,
            }

            async def _call_history(action, params):
                return await asyncio.wait_for(quote.client.call(action, params), timeout=10)

            record, _records = await fetch_onebot_history_record(
                _call_history,
                "get_friend_msg_history",
                expected_message_id=quote.message_id,
                primary_params=primary_params,
                fallback_params=fallback_params,
            )
            if not isinstance(record, dict):
                raise TaskError("host_quote_scope_mismatch", "the outgoing quote has no original peer binding")
            raw = record
        expected_kind = context.scope.kind.value
        if (_identifier(raw.get("message_id")) != quote.message_id
                or raw.get("message_type") != expected_kind
                or raw.get("self_id") is not None and _identifier(raw["self_id"]) != context.scope.account_id
                or expected_kind == "group" and _identifier(raw.get("group_id")) != context.scope.native_scope_id
                or expected_kind == "private" and raw.get("group_id") not in (None, "", 0, "0")
                or (expected_kind == "private" and sender == context.scope.account_id
                    and raw.get("target_id") is not None
                    and _identifier(raw["target_id"]) != context.scope.native_scope_id)
                or raw.get("anonymous") is not None or raw.get("sub_type") == "anonymous"
                or _identifier(raw.get("sender", {}).get("user_id")) != sender
                or raw.get("user_id") is not None and _identifier(raw["user_id"]) != sender
                or quote.sender_id is not None and quote.sender_id != sender):
            raise TaskError("host_quote_scope_mismatch", "quote is not the requested message in the original route")
        if OneBotClient._resolve_call_action(quote.event) != quote.client._call_action:
            raise TaskError("host_quote_account_changed", "the original quote connection changed")
        await check()
        raw_references = OneBotPayloadParser().parse_get_msg_payload(raw)["image_refs"]
        if len(raw_references) > MAX_INPUT_IMAGES:
            raise TaskError("host_image_limit", "quoted images exceed the image intake budget")
        fallback_md5s = _raw_message_image_md5s(raw)
        if resolve_from_original:
            resolved = await ImageResolver(quote.event, quote.client).resolve_for_llm(raw_references)
            if (len(resolved) < len(raw_references)
                    and sender == context.scope.account_id
                    and app is not None):
                # Protocol backends such as SnowLuma may store empty image URLs
                # on bot-sent messages; after scope and recall checks pass,
                # recover exact same-session generated originals by 32-hex MD5.
                app.messages.recalls.check(context.scope, quote.message_id, sender)
                raw_set = set(raw_references)
                resolved_set = set(resolved)
                extra_resolved_iter = iter(item for item in resolved if item not in raw_set)
                rebuilt = []
                for idx, ref in enumerate(raw_references):
                    if ref in resolved_set:
                        rebuilt.append(NativeImageLocator(len(rebuilt), ref, host_resolved=True))
                        continue
                    md5_hex = extract_bare_image_md5(ref)
                    if md5_hex is None and idx < len(fallback_md5s):
                        md5_hex = fallback_md5s[idx]
                    if md5_hex is not None:
                        recovered = await asyncio.to_thread(
                            find_session_generated_image_by_md5, app, context, md5_hex
                        )
                        if recovered is not None:
                            rebuilt.append(_LocalRecoveredImage(len(rebuilt), recovered))
                            continue
                    next_resolved = next(extra_resolved_iter, None)
                    if next_resolved is not None:
                        rebuilt.append(NativeImageLocator(len(rebuilt), next_resolved, host_resolved=True))
                images = tuple(rebuilt)
            else:
                images = tuple(NativeImageLocator(index, value, host_resolved=True)
                               for index, value in enumerate(resolved))
        sent_at = raw.get("time") if type(raw.get("time")) is int and raw["time"] > 0 else sent_at
    _quote_sender(context, sender)
    return images, {"origin": "quoted_message", "original_message_id": quote.message_id,
                    "original_sender": sender, "sent_at_unix": sent_at, "component_position": quote.position}, fallback_md5s


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
        fallback_md5s: tuple[str | None, ...] = ()
        if isinstance(locator, _QuotedImages):
            if locator.message_id == original.event_id:
                raise TaskError("quote_original_cycle", "an input cannot quote itself")
            references, attribution, fallback_md5s = await _quoted_images(locator, context, check, app=app)
            app.messages.recalls.check(context.scope, attribution["original_message_id"], attribution["original_sender"])
        else:
            references, attribution = (locator,), {"origin": "attachment", "component_position": locator.position}
        if len(images) + len(references) > MAX_INPUT_IMAGES:
            raise TaskError("host_image_limit", "native attachments exceed the image intake budget")
        for ref_idx, item in enumerate(references):
            if isinstance(item, _LocalRecoveredImage):
                loaded = await asyncio.to_thread(inspect_image, item.image.content, len(images))
            else:
                try:
                    loaded = await load_native_image(replace(item, position=len(images)))
                except TaskError as error:
                    if (error.code == "host_image_fetch_failed"
                            and attribution.get("origin") == "quoted_message"
                            and attribution.get("original_sender") == context.scope.account_id
                            and ref_idx < len(fallback_md5s)
                            and fallback_md5s[ref_idx] is not None):
                        recovered = await asyncio.to_thread(
                            find_session_generated_image_by_md5, app, context, fallback_md5s[ref_idx]
                        )
                        if recovered is not None:
                            loaded = await asyncio.to_thread(inspect_image, recovered.content, len(images))
                        else:
                            raise
                    else:
                        raise
            images.append(loaded)
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
