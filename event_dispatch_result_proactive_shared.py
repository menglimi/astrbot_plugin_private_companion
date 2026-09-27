# -*- coding: utf-8 -*-
"""EventDispatchResultProactiveMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（13 个方法 + 6 个模块级名字 + 0 个类级赋值 / 1131 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import json
import math
import random
import re
from .event_dispatch_shared import _persona_value, logger
from .helpers import _redact_outbound_secrets, _safe_float, _safe_int, _single_line
from .markdown_segment_guard import MARKDOWN_BLOCK_TOKEN_PATTERN, protect_markdown_blocks
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.message import TextPart
from typing import Any
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None



_SEGMENTED_COMMON_FILE_SUFFIXES = (
    r"(?:7z|aac|apk|avif|avi|bmp|bz2|com|css|csv|docx?|dmg|epub|exe|flac|flv|gif|gz|"
    r"heic|heif|html?|ico|ini|ipa|jar|jpe?g|js|jsonl?|log|m4a|m4v|md|mkv|mobi|mov|"
    r"mp3|mp4|mpeg|mpg|msi|odt|ogg|opus|pdf|png|pptx?|psd|py|rar|raw|rmvb|rtf|svg|"
    r"tar|tgz|tiff?|toml|ts|txt|wav|webm|webp|wmv|wma|xlsx?|xml|xz|ya?ml|zip)"
)

_SEGMENTED_PROTECTED_FILE_SUFFIX_PATTERN = re.compile(
    r"(?i)\." + _SEGMENTED_COMMON_FILE_SUFFIXES + r"(?![\w])"
)

_SEGMENTED_PROTECTED_LITERAL_PATTERN = re.compile(
    r"(?is)"
    r"<(?:image|img|video|record|audio|file)\b[^>]*(?:>.*?</(?:image|img|video|record|audio|file)>|/?>)"
    r"|<tts\b[^>]*>.*?</tts>"
    r"|<[^>\n]{1,240}\bpath=\"[^\"]{1,500}\"[^>\n]*>"
    r"|<[^>\n]{1,240}\b(?:url|src)=\"[^\"]{1,500}\"[^>\n]*>"
    r"|(?i:\b(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)"
    r"|(?i:(?<=[\w\u3400-\u9fff\u3040-\u30ff])\."
    + _SEGMENTED_COMMON_FILE_SUFFIXES
    + r"(?![\w]))"
    r"|(?<![\d.])\d+(?:\.\d+)+(?!\d|\.\d)"
    r"|" + MARKDOWN_BLOCK_TOKEN_PATTERN
)

_SEGMENTED_WIDTH_VARIANT_GROUPS: tuple[tuple[str, ...], ...] = (
    (",", "，"),
    (".", "．", "。"),
    ("?", "？"),
    ("!", "！"),
    (";", "；"),
    (":", "："),
    ("~", "～"),
    ("(", "（"),
    (")", "）"),
    ("[", "［"),
    ("]", "］"),
    ("{", "｛"),
    ("}", "｝"),
    ("<", "＜"),
    (">", "＞"),
    ('"', "＂"),
    ("'", "＇"),
    ("/", "／"),
    ("\\", "＼"),
    ("|", "｜"),
    ("+", "＋"),
    ("-", "－", "—"),
    ("=", "＝"),
    ("*", "＊"),
    ("&", "＆"),
    ("%", "％"),
    ("#", "＃"),
    ("@", "＠"),
    ("$", "＄"),
    ("^", "＾"),
    ("_", "＿"),
)

_SEGMENTED_GENERATED_PUNCTUATION_MARKER = "\ue000"

def _expand_segmented_width_variant_words(words: list[str]) -> list[str]:
    """Add common full-width/half-width punctuation equivalents in stable order."""
    expanded = list(dict.fromkeys(str(word) for word in words if str(word) != ""))
    present = set(expanded)
    for variants in _SEGMENTED_WIDTH_VARIANT_GROUPS:
        configured_widths = {
            len(word)
            for word in tuple(expanded)
            if word and word[0] in variants and word == word[0] * len(word)
        }
        for width in sorted(configured_widths):
            for variant in variants:
                candidate = variant * width
                if candidate not in present:
                    expanded.append(candidate)
                    present.add(candidate)
    return expanded



class _event_dispatch_result_proactiveHostRef:
    """延迟引用宿主 event_dispatch_result_proactive 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import event_dispatch_result_proactive as _host_module

        return getattr(_host_module, name)


_event_dispatch_result_proactive_host = _event_dispatch_result_proactiveHostRef()
