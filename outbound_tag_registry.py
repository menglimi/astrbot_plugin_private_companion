# -*- coding: utf-8 -*-
"""Literal internal tags that may be removed at the outbound boundary.

The registry deliberately contains only markup/sentinel shapes.  It must not
be used to classify ordinary prose as an error or an agent-loop summary.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


@dataclass(frozen=True)
class OwnTag:
    name: str
    owner: Literal["plugin", "astrbot_core", "model_reasoning"]
    kind: Literal["block", "self_closing", "sentinel", "marker", "anchor"]
    pattern: re.Pattern[str]
    truncated: re.Pattern[str] | None = None
    escaped: re.Pattern[str] | None = None
    feature_flag: str | None = None


def _block(name: str, owner: str, *, aliases: tuple[str, ...] = (), feature_flag: str | None = None) -> OwnTag:
    names = "|".join(re.escape(item) for item in (name, *aliases))
    flags = re.IGNORECASE | re.DOTALL
    malformed = (
        re.compile(rf"<\s*(?:{names})[^>]*</\s*(?:{names}|response)\s*>", flags)
        if name in {"thinking", "think", "reasoning"}
        else None
    )
    truncated = rf"<\s*(?:{names})\b[^>]*>[\s\S]*$"
    if malformed is not None:
        truncated = rf"(?:{truncated}|{malformed.pattern})"
    return OwnTag(
        name=name,
        owner=owner,  # type: ignore[arg-type]
        kind="block",
        pattern=re.compile(rf"<\s*(?:{names})\b[^>]*>[\s\S]*?<\s*/\s*(?:{names})\s*>", flags),
        truncated=re.compile(truncated, flags),
        escaped=re.compile(rf"&lt;\s*(?:{names})\b[^&]*&gt;[\s\S]*?&lt;\s*/\s*(?:{names})\s*&gt;|&lt;\s*(?:{names})\b[^&]*$", flags),
        feature_flag=feature_flag,
    )


def _literal(name: str, pattern: str, *, feature_flag: str | None = None) -> OwnTag:
    return OwnTag(
        name=name,
        owner="plugin",
        kind="sentinel",
        pattern=re.compile(pattern, re.IGNORECASE | re.DOTALL),
        feature_flag=feature_flag,
    )


OWN_TAGS: tuple[OwnTag, ...] = (
    _block("timer", "plugin"),
    _block("tts", "plugin", aliases=("pc_tts",)),
    _block("pc_reaction_expression", "plugin"),
    _block("pc_member_safety", "plugin"),
    _block("personality_sync", "plugin"),
    _block("private_companion_context", "plugin"),
    _block("core_memory", "plugin"),
    _block("reference_data", "plugin"),
    _block("system_reminder", "astrbot_core"),
    _block("thinking", "model_reasoning"),
    _block("think", "model_reasoning"),
    _block("reasoning", "model_reasoning"),
    _literal("tts_block", r"\[\[TTSBLOCK:[^\]]*\]\]"),
    _literal("pc_tts_sentinel", r"\[\[PCTTS:[^\]]*\]\]"),
    _literal("photo_silent", r"\[\[PC_PHOTO_SENT_NO_FOLLOWUP\]\]"),
    _literal("history_media", r"<\s*pc[_-]?history[_-]?media(?:[_-]?(?:records?|images?))?\b[^>]*(?:>|/\s*>)|&lt;\s*/?\s*pc[_-]?history[_-]?media[^&]*&gt;"),
    _literal("split", r"<<PRIVATE_COMPANION_SPLIT>>"),
    _literal("qq_anchor", r"(?:\[QQ:[^\]\r\n]{1,160}\]|(?<!\w)QQ:[^\s\r\n]{1,160})"),
    _literal("legacy_marker", r"&&[A-Za-z_][A-Za-z0-9_]*&&"),
    _literal("personality_sync_comment", r"<!--\s*private_companion_personality_sync_v\d+\s*-->|<!--\s*private_companion_personality_sync_v\d+[\s\S]*$"),
    _literal("emotion_control", r"(?<![\w`])\[(?:affectionate|shy|happy|sad|angry|calm|excited|surprised|nervous|scared|worried|upset|frustrated|embarrassed|disgusted|moved|proud|relaxed|grateful|confident|curious|confused|nostalgic|sleepy|thoughtful|yawning|comforting|warm|softly|whispering|laughing|chuckling|sighing)\]", feature_flag="enable_tts_enhancement"),
)


def strip_own_tags(
    text: str,
    *,
    enabled_flags: set[str] | None = None,
    preserve_code_spans: bool = True,
) -> str:
    """Remove registered literal tags while leaving Markdown code examples intact."""
    value = str(text or "")
    enabled = enabled_flags or set()
    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\r\n]+`)", value) if preserve_code_spans else [value]
    for index in range(0, len(parts), 2):
        segment = parts[index]
        for tag in OWN_TAGS:
            if tag.feature_flag and tag.feature_flag not in enabled:
                continue
            replacement = " " if tag.name == "split" else ""
            segment = tag.pattern.sub(replacement, segment)
            if tag.truncated is not None:
                segment = tag.truncated.sub("", segment)
            if tag.escaped is not None:
                segment = tag.escaped.sub("", segment)
        parts[index] = segment
    return "".join(parts)


REGISTERED_TAG_NAMES = frozenset(
    tag.name
    for tag in OWN_TAGS
) | frozenset({
    "pc_tts",
    "pc_history_media",
    "pc_history_media_records",
})

