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


_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    r"<\s*(?:pc_[A-Za-z0-9_-]+|private_companion_[A-Za-z0-9_-]+)(?:\s+[^<>\r\n]{0,160})?/\s*>",
    re.IGNORECASE,
)
_ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    r"&lt;\s*(?:pc_[A-Za-z0-9_-]+|private_companion_[A-Za-z0-9_-]+)(?:\s+[^&\r\n]{0,160})?/\s*&gt;",
    re.IGNORECASE,
)


def _block(
    name: str,
    owner: Literal["plugin", "astrbot_core", "model_reasoning"],
    *,
    aliases: tuple[str, ...] = (),
) -> OwnTag:
    names = "|".join(re.escape(item) for item in (name, *aliases))
    flags = re.IGNORECASE | re.DOTALL
    pattern = rf"<\s*(?:{names})\b[^<>]*>[\s\S]*?<\s*/\s*(?:{names})\s*>"
    escaped = rf"&lt;\s*(?:{names})\b[^&]*&gt;[\s\S]*?&lt;\s*/\s*(?:{names})\s*&gt;"
    if owner == "model_reasoning":
        pattern += rf"|<\s*(?:{names})\b[^<>]*</\s*(?:{names}|response)\s*>"
        escaped += rf"|&lt;\s*(?:{names})\b[^&]*&lt;/\s*(?:{names}|response)\s*&gt;"
    truncated = None
    if name in {"personality_sync", "pc_member_safety"}:
        truncated = re.compile(rf"<\s*(?:{names})\b[^<>]*>[\s\S]*$", flags)
        escaped += rf"|&lt;\s*(?:{names})\b[\s\S]*$"
    return OwnTag(
        name=name,
        owner=owner,
        kind="block",
        pattern=re.compile(pattern, flags),
        truncated=truncated,
        escaped=re.compile(escaped, flags),
    )


def _literal(name: str, pattern: str) -> OwnTag:
    return OwnTag(
        name=name,
        owner="plugin",
        kind="sentinel",
        pattern=re.compile(pattern, re.IGNORECASE | re.DOTALL),
    )


OWN_TAGS: tuple[OwnTag, ...] = (
    OwnTag(
        name="plugin_self_closing",
        owner="plugin",
        kind="self_closing",
        pattern=_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        escaped=_ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
    ),
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
    _literal("split", r"<<PRIVATE_COMPANION_SPLIT>>"),
    _literal("qq_anchor", r"\[QQ[:：]\d{5,12}\]"),
    _literal("legacy_marker", r"&&[A-Za-z_][A-Za-z0-9_]*&&"),
    _literal("personality_sync_comment", r"<!--\s*private_companion_personality_sync_v\d+\s*-->|<!--\s*private_companion_personality_sync_v\d+[\s\S]*$"),
    _literal("emotion_control", r"(?<![\w`])\[(?:affectionate|shy|happy|sad|angry|calm|excited|surprised|nervous|scared|worried|upset|frustrated|embarrassed|disgusted|moved|proud|relaxed|grateful|confident|curious|confused|nostalgic|sleepy|thoughtful|yawning|comforting|warm|softly|whispering|laughing|chuckling|sighing)\]"),
)


def strip_own_tags(
    text: str,
    *,
    tts_enabled: bool = False,
    preserve_code_spans: bool = True,
) -> str:
    """Remove registered literal tags while leaving Markdown code examples intact."""
    value = str(text or "")
    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\r\n]+`)", value) if preserve_code_spans else [value]
    for index in range(0, len(parts), 2):
        segment = parts[index]
        for tag in OWN_TAGS:
            if tag.name == "emotion_control" and not tts_enabled:
                continue
            replacement = " " if tag.name == "split" else ""
            segment = tag.pattern.sub(replacement, segment)
            if tag.truncated is not None:
                segment = tag.truncated.sub("", segment)
            if tag.escaped is not None:
                segment = tag.escaped.sub("", segment)
        parts[index] = segment
    return "".join(parts)
