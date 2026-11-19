"""Recover direct audio/video components before host preprocessing erases provenance."""

from collections.abc import Mapping

from astrbot.core.message.components import At, Forward, Image, Plain, Record, Reply, Video, File
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.native import account_number, native_id
from companion_v2.execution.models import TaskError


def private_components(event):
    message = event.message_obj
    raw = getattr(message, "raw_message", None)
    segments = raw.get("message") if isinstance(raw, Mapping) else None
    if not isinstance(segments, list) or not any(isinstance(s, Mapping) and s.get("type") in ("record", "video", "file") for s in segments):
        return tuple(message.message)
    media = "audio" if any(s.get("type") == "record" for s in segments if isinstance(s, Mapping)) else "video"
    if (raw.get("post_type") != "message" or raw.get("message_type") != "private"
            or raw.get("sub_type", "friend") != "friend" or raw.get("group_id") not in (None, "", 0, "0")
            or native_id(raw.get("message_id")) != native_id(message.message_id)
            or account_number(raw.get("self_id")) != account_number(message.self_id)
            or account_number(raw.get("user_id")) != account_number(message.sender.user_id)
            or not isinstance(raw.get("sender"), Mapping)
            or account_number(raw["sender"].get("user_id")) != account_number(message.sender.user_id)):
        raise TaskError("host_" + media + "_scope_mismatch", "original media event differs from the authenticated private route")
    return _restore_components(segments, media)


def group_components(event):
    """Restore original group media and text; STT output cannot replace their source."""
    group_event_route(event)
    message = event.message_obj
    raw = getattr(message, "raw_message", None)
    segments = raw.get("message") if isinstance(raw, Mapping) else None
    if not isinstance(segments, list) or not any(
            isinstance(s, Mapping) and s.get("type") in ("record", "video", "file") for s in segments):
        return tuple(message.message)
    return _restore_components(segments, "group_media", group=True)


def _restore_components(segments, media, *, group=False):
    if not 1 <= len(segments) <= 128:
        raise TaskError("host_" + media + "_limit", "original media message has too many components")
    result = []
    for segment in segments:
        if not isinstance(segment, Mapping) or not isinstance(segment.get("data"), Mapping):
            raise TaskError("host_" + media + "_invalid", "original media components are malformed")
        kind, data = segment.get("type"), segment["data"]
        if kind == "text" and type(data.get("text")) is str:
            result.append(Plain(data["text"]))
        elif kind in ("image", "record", "video"):
            # Never carry STT/TTS text, path, caption or a local-file resolver.
            cls = {"image": Image, "record": Record, "video": Video}[kind]
            result.append(cls(file=data.get("file", ""), url=data.get("url", "")))
        elif kind == "reply":
            result.append(Reply(id=str(data["id"])))
        elif kind == "forward":
            result.append(Forward(id=str(data["id"])))
        elif kind == "file":
            result.append(File(name=data.get("file_name") or data.get("name") or data.get("file") or "附件",
                file=data.get("file_id") or data.get("id") or data.get("file") or "", url=data.get("url") or ""))
        elif kind == "at" and group:
            result.append(At(qq=str(data["qq"])))
        else:
            raise TaskError("host_media_unsupported", "this original component needs its own governed source adapter")
    return tuple(result)
