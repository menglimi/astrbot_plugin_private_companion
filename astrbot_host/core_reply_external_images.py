"""Real-time capture of images produced by external LLM tools into the role album.

Supports two delivery paths:
  1. Direct ``event.send(MessageChain([Image(...)]))`` inside an active external
     LLM tool (when ``delivery_guard_active`` is ``False``).
  2. External tool returning ``CallToolResult(ImageContent)`` cached by AstrBot
     and subsequently delivered via ``send_message_to_user``.

Four guardrails prevent false captures (T2I text cards, stickers/memes,
screenshots, icons, QR codes, or failed sends):
  1. Strict active external-tool window / cached-image provenance.
  2. Two-tier tool admission (Tier 1 zero-dependency dict metadata, Tier 2
     strict non-generation blacklist + explicit image-generation signals).
  3. Static single-frame PNG/JPEG/WEBP quality check (min edge >= 256px, <= 20 MiB).
  4. Same-scope cross-ledger SHA-256 deduplication + async ``media_submit`` write.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import logging
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlparse

from astrbot.core.message.components import Image

from companion_v2.conversation.media.external_generated_album import (
    EXTERNAL_MAX_BYTES,
    allowed_origin_keys,
    record_external_generated_image,
)
from companion_v2.execution.models import TaskContext

from .core_reply import CORE_REPLY_CONTEXT_EXTRA


EXTERNAL_META_ATTR = "_companion_generated_image_meta"
_ACTIVE_TOOL_TTL_SECONDS = 600.0
_RECENT_TOOL_CAP = 32
_INTERNAL_TOOL_PREFIXES = ("companion_v2_", "astrbot_")
_INTERNAL_TOOL_EXACT = frozenset({"send_message_to_user"})
_BLACKLIST_TOKENS = frozenset({
    "browser", "screenshot", "screen_capture", "capture_screen", "webpage",
    "playwright", "selenium", "camera", "webcam", "snapshot",
    "reaction", "sticker", "meme", "emoji", "gif", "avatar", "profile",
    "thumbnail", "thumb",
    "qr", "qrcode", "barcode", "captcha",
    "chart", "graph", "plot", "mermaid", "diagram", "latex", "math",
    "markdown", "render_text", "t2i", "text_to_image_card",
    "tts", "voice", "audio", "music", "video",
    "search", "web_search", "fetch", "scrape", "weather", "map", "calendar",
    "ocr", "vision", "analyze", "describe", "inspect", "read_file", "download",
})
_GENERATION_NAME_TOKENS = frozenset({
    "nai", "novelai", "draw", "paint", "imagine", "flux", "sdxl",
    "comfy", "comfyui", "midjourney", "dalle", "imagen",
    "txt2img", "text2img", "img2img", "inpaint", "outpaint", "genimg",
})
_GENERATION_NAME_PHRASES = (
    "generate_image", "image_generate", "gen_img",
    "make_image", "create_image", "render_image",
    "edit_image", "image_edit", "dall_e",
    "生图", "画图", "绘图", "改图", "文生图", "图生图",
)
_IMAGE_NOUN_KEYWORDS = ("image", "img", "pic", "picture", "photo", "art", "illust", "图", "画")
_PROMPT_ARG_KEYS = ("prompt", "positive_prompt", "tags", "tag", "image_prompt", "description")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
logger = logging.getLogger(__name__)


def _capture_state(event) -> dict | None:
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return None
    return core.setdefault(
        "_external_image_capture",
        {"seq": 0, "active": [], "recent": {}, "cached": {}},
    )


def _tool_name(tool) -> str:
    if isinstance(tool, str):
        return tool.strip()
    name = getattr(tool, "name", "")
    return name.strip() if isinstance(name, str) else ""


def _tool_description(tool) -> str:
    desc = getattr(tool, "description", "")
    return desc.strip() if isinstance(desc, str) else ""


def _extract_prompt_from_args(tool_args) -> str:
    if not isinstance(tool_args, Mapping):
        return ""
    for key in _PROMPT_ARG_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_meta_dict(raw) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    out = {}
    for field in ("prompt", "model", "size", "source_plugin", "tool_name"):
        val = raw.get(field)
        if isinstance(val, str) and val.strip():
            out[field] = val.strip()
    return out


def is_internal_tool(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if lowered in _INTERNAL_TOOL_EXACT:
        return True
    return lowered.startswith(_INTERNAL_TOOL_PREFIXES)


def is_tier2_image_generation_tool(
    tool_name: str,
    *,
    description: str = "",
    tool_args: Mapping | None = None,
) -> bool:
    """Generic Tier-2 heuristic: strict non-generation blacklist + explicit generation signal."""
    raw_name = (tool_name or "").strip()
    if not raw_name or is_internal_tool(raw_name):
        return False
    normalized = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", raw_name).lower()
    tokens = {tok for tok in _TOKEN_SPLIT_RE.split(normalized) if tok}
    # Reject blacklisted tokens either as exact segment or obvious compound substring
    if tokens & _BLACKLIST_TOKENS:
        return False
    for blocked in (
        "screenshot", "qrcode", "barcode", "captcha", "mermaid",
        "sticker", "reaction", "webcam", "thumbnail",
    ):
        if blocked in normalized:
            return False
    # Positive signal 1: exact generation token OR unambiguous compound/CJK phrase
    if tokens & _GENERATION_NAME_TOKENS:
        return True
    for phrase in _GENERATION_NAME_PHRASES:
        if phrase in normalized:
            return True
    if ("generate" in tokens or "gen" in tokens or "create" in tokens or "make" in tokens) and any(
        noun in normalized for noun in _IMAGE_NOUN_KEYWORDS
    ):
        return True
    # Positive signal 2: description/name indicates image + non-empty prompt arg
    prompt = _extract_prompt_from_args(tool_args)
    if prompt:
        combined = f"{normalized} {(description or '').lower()}"
        if any(noun in combined for noun in _IMAGE_NOUN_KEYWORDS):
            # Still reject description-level non-generation utilities (e.g. search/analyze)
            if not any(word in tokens for word in ("search", "query", "find", "list", "get")):
                return True
    return False


def _prune_active(state: dict, now_mono: float) -> None:
    state["active"] = [
        entry
        for entry in state["active"]
        if (now_mono - entry["started_at"]) <= _ACTIVE_TOOL_TTL_SECONDS
    ]


def clear_active_external_tools(event) -> None:
    """Close any stale active external-tool window when entering guarded delivery or cleanup."""
    state = _capture_state(event)
    if state is not None:
        state["active"].clear()


def observe_external_tool_begin(event, tool, tool_args=None) -> bool:
    """Record the start of a non-internal LLM tool execution on this event."""
    state = _capture_state(event)
    if state is None:
        return False
    name = _tool_name(tool)
    if not name or is_internal_tool(name):
        # Starting an internal tool isolates it from any stale external tool
        # whose exception bypassed on_llm_tool_respond.
        state["active"].clear()
        return False
    now_mono = time.monotonic()
    _prune_active(state, now_mono)
    desc = _tool_description(tool)
    prompt = _extract_prompt_from_args(tool_args)
    tier2 = is_tier2_image_generation_tool(name, description=desc, tool_args=tool_args)
    state["seq"] += 1
    entry = {
        "id": state["seq"],
        "name": name,
        "description": desc,
        "prompt": prompt,
        "size": str(tool_args.get("size") or tool_args.get("resolution") or "").strip()
        if isinstance(tool_args, Mapping)
        else "",
        "model": str(tool_args.get("model") or "").strip() if isinstance(tool_args, Mapping) else "",
        "tier2_eligible": tier2,
        "started_at": now_mono,
    }
    state["active"].append(entry)
    recent = state["recent"]
    recent[name] = entry
    while len(recent) > _RECENT_TOOL_CAP:
        recent.pop(next(iter(recent)))
    return True


def observe_external_tool_end(event, tool, tool_args=None, tool_result=None) -> bool:
    """Close the matching active external-tool window after the tool returns."""
    del tool_args, tool_result
    state = _capture_state(event)
    if state is None:
        return False
    name = _tool_name(tool)
    if not name:
        return False
    active = state["active"]
    for index in range(len(active) - 1, -1, -1):
        if active[index]["name"] == name:
            active.pop(index)
            return True
    return False


def _resolve_application_and_context(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return None, None, None
    prepared = core.get("prepared")
    context = prepared.get("context") if isinstance(prepared, Mapping) else None
    if not isinstance(context, TaskContext):
        return None, None, None
    application = core.get("application")
    if application is None:
        tool_session = core.get("_tool_session")
        application = getattr(tool_session, "application", None)
    if application is None or getattr(application, "runtime", None) is None:
        return None, None, None
    return core, application, context


def _resolve_local_file_path(value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith(("base64://", "http://", "https://", "data:")):
        return None
    if text.startswith("file://"):
        parsed = urlparse(text)
        raw_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raw_path = f"//{parsed.netloc}{raw_path}"
        elif re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        candidate = Path(raw_path)
    else:
        candidate = Path(text)
    try:
        if candidate.is_file():
            return candidate.resolve(strict=False)
    except OSError:
        return None
    return None


def _snapshot_image_source(part: Image) -> tuple[str, object] | None:
    """Extract a lightweight snapshot (base64 string or local bytes) without network I/O."""
    file_val = getattr(part, "file", None)
    if isinstance(file_val, str) and file_val.startswith("base64://"):
        encoded = file_val[len("base64://"):]
        if encoded:
            return ("base64", encoded)
    for attr in ("path", "file"):
        local = _resolve_local_file_path(getattr(part, attr, None))
        if local is not None:
            try:
                size = local.stat().st_size
                if 0 < size <= EXTERNAL_MAX_BYTES:
                    return ("bytes", local.read_bytes())
            except OSError:
                continue
    return None


def _decode_snapshot(kind: str, payload: object) -> bytes | None:
    if kind == "bytes":
        return payload if isinstance(payload, bytes) else None
    if kind == "base64" and isinstance(payload, str):
        try:
            return base64.b64decode(payload, validate=False)
        except (binascii.Error, ValueError):
            return None
    return None


def _schedule_record(core: dict, application, context: TaskContext, items: list[tuple[tuple[str, object], dict]], *, submit=None) -> bool:
    if not items:
        return False
    runtime = application.runtime
    now_fn = getattr(application, "now", None)
    observed_at = now_fn() if callable(now_fn) else None
    dispatcher = submit if callable(submit) else core.get("media_submit")

    def worker():
        origin_keys = allowed_origin_keys(runtime, context)
        for (snap_kind, snap_payload), meta in items:
            raw = _decode_snapshot(snap_kind, snap_payload)
            if not raw:
                continue
            try:
                outcome = record_external_generated_image(
                    runtime,
                    origin_key=context.key,
                    content=raw,
                    created_at=observed_at,
                    tool_name=meta.get("tool_name", ""),
                    prompt=meta.get("prompt", ""),
                    model=meta.get("model", ""),
                    size=meta.get("size", ""),
                    source_plugin=meta.get("source_plugin", ""),
                    extra_origin_keys=origin_keys,
                )
                if outcome.get("status") == "recorded":
                    logger.info(
                        "[CompanionV2] 已自动收录外部生图工具产出 image_id=%s tool=%s",
                        outcome.get("image_id"),
                        meta.get("tool_name") or "external",
                    )
            except ValueError:
                # Guardrail 3 rejection (e.g. animated GIF, small icon, invalid format)
                continue
            except Exception as error:
                logger.warning(
                    "[CompanionV2] 外部生图工具产出收录失败 tool=%s error_type=%s",
                    meta.get("tool_name") or "external",
                    type(error).__name__,
                )

    if callable(dispatcher):
        return dispatcher(worker) is not None
    worker()
    return True


def observe_external_tool_send_returned(event, message, *, message_meta=None) -> bool:
    """Path 1: capture images after direct ``event.send(...)`` succeeds inside an external tool."""
    state = _capture_state(event)
    if state is None:
        return False
    core, application, context = _resolve_application_and_context(event)
    if core is None or core.get("delivery_guard_active"):
        return False
    _prune_active(state, time.monotonic())
    active = state["active"]
    if not active:
        return False
    current_tool = active[-1]
    if is_internal_tool(current_tool["name"]):
        return False
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return False

    chain_meta = _extract_meta_dict(
        message_meta if message_meta is not None else getattr(message, EXTERNAL_META_ATTR, None)
    )
    items = []
    for part in chain:
        if not isinstance(part, Image):
            continue
        part_meta = _extract_meta_dict(getattr(part, EXTERNAL_META_ATTR, None))
        explicit_meta = part_meta if part_meta is not None else chain_meta
        if explicit_meta is None and not current_tool["tier2_eligible"]:
            continue
        snapshot = _snapshot_image_source(part)
        if snapshot is None:
            continue
        merged_meta = {
            "tool_name": (explicit_meta or {}).get("tool_name") or current_tool["name"],
            "prompt": (explicit_meta or {}).get("prompt") or current_tool.get("prompt", ""),
            "model": (explicit_meta or {}).get("model") or current_tool.get("model", ""),
            "size": (explicit_meta or {}).get("size") or current_tool.get("size", ""),
            "source_plugin": (explicit_meta or {}).get("source_plugin") or "",
        }
        items.append((snapshot, merged_meta))
    if not items:
        return False
    return _schedule_record(core, application, context, items)


def observe_external_cached_image(event, cached_image) -> bool:
    """Path 2 (stage A): record cached image provenance yielded by an eligible external tool."""
    state = _capture_state(event)
    if state is None:
        return False
    tool_name = getattr(cached_image, "tool_name", "")
    if not isinstance(tool_name, str) or not tool_name.strip() or is_internal_tool(tool_name):
        return False
    tool_name = tool_name.strip()
    file_path = getattr(cached_image, "file_path", None)
    if not isinstance(file_path, str) or not file_path.strip():
        return False
    explicit_meta = _extract_meta_dict(getattr(cached_image, EXTERNAL_META_ATTR, None))
    tool_entry = None
    for entry in reversed(state["active"]):
        if entry["name"] == tool_name:
            tool_entry = entry
            break
    if tool_entry is None:
        tool_entry = state["recent"].get(tool_name)
    eligible = explicit_meta is not None or (
        tool_entry["tier2_eligible"]
        if tool_entry is not None
        else is_tier2_image_generation_tool(tool_name)
    )
    if not eligible:
        return False
    original = Path(file_path).resolve(strict=False)
    record = {
        "path": original,
        "meta": {
            "tool_name": (explicit_meta or {}).get("tool_name") or tool_name,
            "prompt": (explicit_meta or {}).get("prompt") or (tool_entry or {}).get("prompt", ""),
            "model": (explicit_meta or {}).get("model") or (tool_entry or {}).get("model", ""),
            "size": (explicit_meta or {}).get("size") or (tool_entry or {}).get("size", ""),
            "source_plugin": (explicit_meta or {}).get("source_plugin") or "",
        },
    }
    state["cached"][str(original)] = record
    state["cached"][original.as_uri()] = record
    state["cached"][file_path] = record
    return True


def observe_external_media_send_returned(event, message, *, submit=None) -> bool:
    """Path 2 (stage B): capture cached external images after ``send_message_to_user`` succeeds."""
    state = _capture_state(event)
    if state is None or not state["cached"]:
        return False
    core, application, context = _resolve_application_and_context(event)
    if core is None:
        return False
    chain = getattr(message, "chain", ())
    matched = []
    seen_paths = set()
    for part in chain:
        if not isinstance(part, Image):
            continue
        entry = None
        for raw_ref in (getattr(part, "path", None), getattr(part, "file", None)):
            if not isinstance(raw_ref, str) or not raw_ref:
                continue
            entry = state["cached"].get(raw_ref)
            if entry is None:
                resolved = _resolve_local_file_path(raw_ref)
                if resolved is not None:
                    entry = state["cached"].get(str(resolved)) or state["cached"].get(resolved.as_uri())
            if entry is not None:
                break
        if entry is None:
            continue
        path_key = str(entry["path"])
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        try:
            if not entry["path"].is_file() or entry["path"].stat().st_size > EXTERNAL_MAX_BYTES:
                continue
            raw_bytes = entry["path"].read_bytes()
        except OSError:
            continue
        matched.append((("bytes", raw_bytes), dict(entry["meta"])))
    if not matched:
        return False
    return _schedule_record(core, application, context, matched, submit=submit)
