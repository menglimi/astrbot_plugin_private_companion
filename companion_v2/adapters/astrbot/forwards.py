"""Account-pinned forward reads; freeze the returned tree before image awaits."""

import asyncio
from dataclasses import dataclass

from companion_v2.conversation.forwards.models import (CapturedForward, CapturedForwardNode, MAX_TEXT,
                                                       ForwardIntakePolicy)
from companion_v2.conversation.media.models import CapturedImage, MAX_INPUT_IMAGES, fits_budget
from companion_v2.conversation.media.audio.models import CapturedAudio, MAX_INPUT_AUDIOS, fits_audio_budget
from companion_v2.conversation.media.video.models import CapturedVideo, MAX_VIDEOS, fits_video_budget
from companion_v2.conversation.quotes.models import message_number
from companion_v2.execution.models import TaskError, digest

from .native import account_number
from .history import fetch_onebot_history_record, parse_signed_int
from .quotes import image_locator


def resource_id(value):
    if type(value) not in (str, int):
        raise TaskError("host_forward_invalid", "forward needs its native resource identifier")
    value = str(value)
    if not 0 < len(value) <= 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise TaskError("host_forward_invalid", "forward resource identifier is invalid")
    return value


@dataclass(frozen=True)
class NativeForwardLocator:
    position: int
    resource: str

    def __post_init__(self):
        if type(self.position) is not int or not 0 <= self.position < 128 or resource_id(self.resource) != self.resource:
            raise TaskError("host_forward_invalid", "forward locator must retain the native component position")


@dataclass(frozen=True)
class _NodePlan:
    path: tuple
    resource_hash: str
    sender_id: str | None
    sender_name: str
    sent_at: int | None
    text: str
    images: tuple
    audios: tuple
    videos: tuple


def _displayed_account(value):
    if value in (None, "", 0, "0") and type(value) is not bool:
        return None
    value = account_number(value)
    if len(value) > 20:
        raise TaskError("host_forward_invalid", "displayed account exceeds the node contract")
    return value


def freeze_nodes(records, resource_hash, *, audio_enabled=True, video_enabled=True, intake=ForwardIntakePolicy()):
    from .quotes import audio_locator, video_locator
    max_nodes, max_depth = intake.max_nodes, intake.max_depth
    result = []

    def visit(records, prefix, resource_hash):
        if (len(prefix) >= 2 * max_depth or not isinstance(records, list) or not records
                or len(records) + len(result) > max_nodes):
            raise TaskError("host_forward_limit", "forward tree exceeds its node or depth budget")
        for index, raw in enumerate(records):
            if len(result) >= max_nodes or not isinstance(raw, dict):
                raise TaskError("host_forward_limit", "forward tree exceeds its node budget")
            path = (*prefix, index)
            if "type" in raw:
                if raw["type"] != "node":
                    raise TaskError("host_forward_unsupported", "expanded records require messages or node wrappers")
                raw = raw.get("data")
            if not isinstance(raw, dict):
                raise TaskError("host_forward_invalid", "returned node has no component body")
            sender = raw.get("sender", {})
            if not isinstance(sender, dict):
                raise TaskError("host_forward_invalid", "returned node has invalid displayed attribution")
            sender_id = _displayed_account(sender.get("user_id", raw.get("user_id")))
            if raw.get("user_id") is not None and _displayed_account(raw["user_id"]) != sender_id:
                raise TaskError("host_forward_invalid", "node account fields disagree")
            name = sender.get("nickname", raw.get("nickname", ""))
            if type(name) is not str or len(name) > 256:
                raise TaskError("host_forward_limit", "node display name exceeds its text budget")
            at = raw.get("time")
            at = at if type(at) is int and 0 < at <= 253402300799 else None
            message, content = raw.get("message"), raw.get("content")
            if message and content and message != content:
                raise TaskError("host_forward_invalid", "node has ambiguous component arrays")
            parts = message or content
            if not isinstance(parts, list) or not parts:
                raise TaskError("host_forward_unsupported", "forward nodes require native component arrays")
            if len(parts) > 128:
                raise TaskError("host_forward_limit", "forward node exceeds its component budget")
            text, images, audios, videos, nested = [], [], [], [], []
            for position, part in enumerate(parts):
                if not isinstance(part, dict) or not isinstance(part.get("data"), dict):
                    raise TaskError("host_forward_unsupported", "forward component requires a native data object")
                kind, data = part.get("type"), part["data"]
                if kind == "text" and type(data.get("text")) is str:
                    text.append(data["text"])
                elif kind == "image":
                    images.append(image_locator(position, data.get("file"), data.get("url")))
                    text.append("\n[图片 " + str(len(images)) + "]\n")
                elif kind == "record":
                    if not audio_enabled:
                        raise TaskError("host_forward_unsupported", "forward voice decoder is unavailable")
                    audios.append(audio_locator(position, data.get("file"), data.get("url")))
                    text.append("\n[语音 " + str(len(audios)) + "]\n")
                elif kind == "video":
                    if not video_enabled:
                        raise TaskError("host_forward_unsupported", "forward video decoder is unavailable")
                    videos.append(video_locator(position, data.get("file"), data.get("url")))
                    text.append("\n[视频 " + str(len(videos)) + "]\n")
                elif kind in ("forward", "node") and not intake.parse_nested:
                    text.append("\n[嵌套转发未展开 组件 " + str(position) + "]\n")
                elif kind in ("forward", "node"):
                    children = data.get("content") if kind == "forward" else [part]
                    # NapCat expands nested contents with the outer read. Its
                    # inner node IDs are display-only; do not use get_msg or
                    # widen the query to other conversations to resolve them.
                    if kind == "forward" and not isinstance(children, list):
                        raise TaskError("host_forward_unsupported", "nested forward did not return its expanded nodes")
                    nested_hash = digest(resource_id(data["id"])) if kind == "forward" and data.get("id") is not None else digest([resource_hash, path, position])
                    nested.append((children, (*path, position), nested_hash))
                    text.append("\n[嵌套转发 组件 " + str(position) + "]\n")
                else:
                    raise TaskError("host_forward_unsupported", "forwarded component needs its own governed adapter")
            body = "".join(text)
            if (not body.strip() or len(body) > MAX_TEXT or len(images) > MAX_INPUT_IMAGES
                    or len(audios) > MAX_INPUT_AUDIOS or len(videos) > MAX_VIDEOS):
                raise TaskError("host_forward_limit", "returned node exceeds its text or media budget")
            result.append(_NodePlan(path, resource_hash, sender_id, name, at, body,
                                    tuple(images), tuple(audios), tuple(videos)))
            for children, child_path, child_hash in nested:
                visit(children, child_path, child_hash)
    visit(records, (), resource_hash)
    return tuple(result)


def freeze_forward_cards(locators, cards, *, audio_enabled=True, video_enabled=True, intake=ForwardIntakePolicy()):
    """Validate and freeze all cards before any attachment download."""
    if (not isinstance(locators, tuple) or not 0 < len(locators) <= intake.max_forwards
            or any(type(item) is not NativeForwardLocator for item in locators)
            or tuple(l.position for l in locators) != tuple(sorted({l.position for l in locators}))):
        raise TaskError("host_forward_limit", "input needs bounded ordered native forward locators")
    if len(cards) != len(locators):
        raise TaskError("host_forward_scope_mismatch", "history changed the number of original forward components")
    plans = []
    for locator, card in zip(locators, cards):
        if not isinstance(card, dict) or resource_id(card.get("id")) != locator.resource:
            raise TaskError("host_forward_scope_mismatch", "history changed the ordered forward identifiers")
        if not isinstance(card.get("content"), list):
            raise TaskError("host_forward_unsupported", "forward did not return its expanded nodes")
        plans.append((locator, freeze_nodes(card["content"], digest(locator.resource),
                                            audio_enabled=audio_enabled, video_enabled=video_enabled, intake=intake)))
    all_nodes = [n for _, nodes in plans for n in nodes]
    if (len(all_nodes) > intake.max_nodes or sum(len(n.text) + len(n.sender_name) for n in all_nodes) > MAX_TEXT
            or sum(len(n.images) for n in all_nodes) > MAX_INPUT_IMAGES
            or sum(len(n.audios) for n in all_nodes) > MAX_INPUT_AUDIOS
            or sum(len(n.videos) for n in all_nodes) > MAX_VIDEOS):
        raise TaskError("host_forward_limit", "forward records exceed their shared intake budget")
    return tuple(plans)


async def capture_forward_cards(plans, *, image_loader, current, audio_loader=None, video_loader=None,
                                retained=(), retained_audios=(), retained_videos=()):
    result, retained = [], list(retained)
    retained_audios, retained_videos = list(retained_audios), list(retained_videos)
    for forward_locator, nodes in plans:
        captured = []
        for node in nodes:
            images, audios, videos = [], [], []
            for image in node.images:
                current()
                original = await image_loader(image)
                current()
                if not isinstance(original, CapturedImage) or original.descriptor.position != image.position:
                    raise TaskError("host_image_invalid", "forward image loader changed the original position")
                images.append(original)
                retained.append(original.descriptor)
                if (not fits_budget(retained)
                        or not fits_video_budget(tuple(retained_videos), retained, tuple(retained_audios))):
                    raise TaskError("host_forward_limit", "forward media exceeds its shared intake budget")
            for locator in node.audios:
                if audio_loader is None:
                    raise TaskError("host_forward_unsupported", "forward voice decoder is unavailable")
                current()
                original = await audio_loader(locator)
                current()
                if type(original) is not CapturedAudio or original.descriptor.position != locator.position:
                    raise TaskError("host_audio_invalid", "forward audio loader changed the original position")
                audios.append(original)
                retained_audios.append(original.descriptor)
                if (not fits_audio_budget(tuple(retained_audios), retained)
                        or not fits_video_budget(tuple(retained_videos), retained, tuple(retained_audios))):
                    raise TaskError("host_forward_limit", "forward voice exceeds its shared audio budget")
            for locator in node.videos:
                if video_loader is None:
                    raise TaskError("host_forward_unsupported", "forward video decoder is unavailable")
                current()
                original = await video_loader(locator)
                current()
                if type(original) is not CapturedVideo or original.descriptor.position != locator.position:
                    raise TaskError("host_video_invalid", "forward video loader changed the original position")
                videos.append(original)
                retained_videos.append(original.descriptor)
                if not fits_video_budget(tuple(retained_videos), retained, tuple(retained_audios)):
                    raise TaskError("host_forward_limit", "forward video exceeds its shared media budget")
            captured.append(CapturedForwardNode(node.path, node.resource_hash, node.sender_id, node.sender_name,
                                                 node.sent_at, node.text, tuple(images), tuple(audios), tuple(videos)))
        result.append(CapturedForward(forward_locator.position, digest(forward_locator.resource), tuple(captured)))
    current()
    return tuple(result)


async def read_private_forwards(locators, *, message_id, client, scope, reverse_api_type, image_loader,
                                audio_loader=None, video_loader=None, intake=ForwardIntakePolicy()):
    try:
        if (not isinstance(locators, tuple) or not 0 < len(locators) <= intake.max_forwards
                or any(type(item) is not NativeForwardLocator for item in locators)):
            raise TaskError("host_forward_limit", "input needs bounded native forward locators")
        api, clients = client._api._wsr_api, client._wsr_api_clients
        connection = clients.get(scope.account_id)
        def current():
            if (type(api) is not reverse_api_type or api._api_clients is not clients or not connection
                    or client._api._wsr_api is not api or client._wsr_api_clients is not clients
                    or clients.get(scope.account_id) is not connection):
                raise TaskError("host_forward_account_changed", "original forward account connection changed")
        current()
        # NapCat sets each forward.id to the containing message ID. Its
        # get_forward_msg reads only the first component, so that API cannot
        # distinguish multiple cards in one message. Read this exact outer
        # message from the pinned peer, with all forward contents expanded.
        sequence = message_number(message_id, error_code="host_forward_invalid")
        peer_int = parse_signed_int(scope.native_scope_id)
        msg_int = parse_signed_int(sequence)
        primary_params = {
            "self_id": scope.account_id,
            "user_id": scope.native_scope_id,
            "message_seq": sequence,
            "count": 1,
            "reverse_order": False,
            "parse_mult_msg": True,
        }
        fallback_params = {
            "self_id": scope.account_id,
            "user_id": peer_int if peer_int is not None else scope.native_scope_id,
            "message_id": msg_int if msg_int is not None else sequence,
            "count": 1,
            "reverse_order": False,
        }

        async def _call_history(action, params):
            return await api.call_action(action, **params)

        outer, _records = await fetch_onebot_history_record(
            _call_history,
            "get_friend_msg_history",
            expected_message_id=sequence,
            primary_params=primary_params,
            fallback_params=fallback_params,
            after_call=current,
        )
        if not isinstance(outer, dict):
            raise TaskError("host_forward_unavailable", "history did not return the original outer message")
        if (outer.get("message_type") != "private" or outer.get("sub_type", "friend") != "friend"
                or outer.get("group_id") not in (None, "", 0, "0")
                or (outer.get("self_id") is not None and account_number(outer["self_id"]) != scope.account_id)
                or message_number(outer.get("message_id"), error_code="host_forward_scope_mismatch") != sequence
                or account_number(outer.get("sender", {}).get("user_id")) != scope.native_scope_id
                or (outer.get("user_id") is not None and account_number(outer["user_id"]) != scope.native_scope_id)):
            raise TaskError("host_forward_scope_mismatch", "forward history belongs to another message or private participant")
        parts = outer.get("message")
        if not isinstance(parts, list) or not 0 < len(parts) <= 128 or any(not isinstance(p, dict) for p in parts):
            raise TaskError("host_forward_unsupported", "outer message requires a native component array")
        cards = [p.get("data") for p in parts if p.get("type") == "forward"]
        plans = freeze_forward_cards(locators, cards, audio_enabled=audio_loader is not None,
                                     video_enabled=video_loader is not None, intake=intake)
        return await capture_forward_cards(plans, image_loader=image_loader, audio_loader=audio_loader,
                                           video_loader=video_loader, current=current)
    except TaskError:
        raise
    except asyncio.TimeoutError:
        raise TaskError("host_forward_timeout", "forward preparation did not complete in time") from None
    except Exception:
        raise TaskError("host_forward_unavailable", "forwarded records could not be captured") from None
