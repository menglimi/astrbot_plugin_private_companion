# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import statistics
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .helpers import _safe_float, _safe_int, _single_line
from .reaction_asset_index import ReactionAssetLookupIndex
from .reaction_asset_usage import ReactionAssetUsageStore


CATALOG_VERSION = 2
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 120 * 1024 * 1024
MAX_ZIP_MEMBERS = 1000
LOOKUP_CACHE_TTL_SECONDS = 2.0
MAX_EMBEDDING_DIMENSION = 8192
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ANALYSIS_STATUSES = {"unprocessed", "pending", "running", "complete", "failed"}
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# Lightweight, local semantic equivalence used when no embedding provider is
# available.  These are deliberately small communication-oriented clusters,
# rather than a general thesaurus: a shared cluster only adds a soft score and
# never replaces explicit tags or a caller-supplied intent.
_SEMANTIC_MATCH_CLUSTERS: dict[str, tuple[str, ...]] = {
    "开心喜悦": ("开心", "高兴", "快乐", "愉快", "喜悦", "欢喜", "兴奋", "好耶", "庆祝", "鼓掌"),
    "安慰陪伴": ("安慰", "抱抱", "抱一抱", "陪伴", "哄哄", "哄一下", "心疼", "安抚", "鼓励", "加油", "没关系", "别难过", "别伤心"),
    "无语无奈": ("无语", "无奈", "服了", "不想说话", "说不出话", "沉默", "叹气", "扶额", "摊手"),
    "害羞脸红": ("害羞", "腼腆", "不好意思", "脸红", "扭捏"),
    "难过委屈": ("难过", "伤心", "低落", "委屈", "不开心", "不高兴", "想哭", "哭哭"),
    "惊讶意外": ("惊讶", "震惊", "意外", "吃惊", "啊这"),
    "生气恼火": ("生气", "恼火", "气愤", "发火", "愤怒"),
    "吐槽接梗": ("吐槽", "嫌弃", "质疑", "调侃", "接梗", "开玩笑"),
    "赞同回应": ("赞同", "同意", "认可", "点头", "收到", "可以"),
    "拒绝摇头": ("拒绝", "不要", "才不要", "不行", "摇头", "走开"),
}
_SEMANTIC_NEGATION_PATTERN = re.compile(
    r"(?:不是|并非|不|没|未|别|莫|无)(?:太|很|怎么|再|够|那么|特别)?$"
)


def _semantic_features(value: Any) -> tuple[set[str], set[str]]:
    """Return (cluster names, aliases blocked by a nearby negation)."""
    text = re.sub(r"[\W_]+", "", str(value or "").casefold())
    if not text:
        return set(), set()
    clusters: set[str] = set()
    blocked: set[str] = set()
    for cluster, aliases in _SEMANTIC_MATCH_CLUSTERS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            alias_key = alias.casefold()
            start = text.find(alias_key)
            while start >= 0:
                prefix = text[max(0, start - 8) : start]
                negated = bool(_SEMANTIC_NEGATION_PATTERN.search(prefix))
                if negated:
                    blocked.add(alias_key)
                else:
                    clusters.add(cluster)
                start = text.find(alias_key, start + max(1, len(alias_key)))
    return clusters, blocked


def _text_list(value: Any, *, limit: int, item_limit: int = 60) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = re.split(r"[,，;；|\n]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _single_line(item, item_limit)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _query_list(value: Any, *, limit: int = 8, item_limit: int = 160) -> list[str]:
    """Normalize a small list of lookup phrases from tool/context payloads."""
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        text = str(value or "").strip()
        parsed: Any = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        raw = list(parsed) if isinstance(parsed, list) else re.split(r"[,，;；|\n]+", text)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _single_line(item, item_limit).strip(" \t\"'")
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max(1, int(limit)):
            break
    return result


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "y", "是", "开启", "启用"}:
        return True
    if normalized in {"0", "false", "no", "off", "n", "否", "关闭", "停用"}:
        return False
    return default


def _safe_filename(value: Any, fallback: str = "reaction") -> str:
    name = Path(str(value or "")).name
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", name).strip(" ._")
    return stem[:120] or fallback


def _image_signature_matches(data: bytes, extension: str) -> bool:
    extension = extension.lower()
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension == ".gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if extension == ".bmp":
        return data.startswith(b"BM")
    return False



class _reaction_asset_libraryHostRef:
    """延迟引用宿主 reaction_asset_library 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import reaction_asset_library as _host_module

        return getattr(_host_module, name)


_reaction_asset_library_host = _reaction_asset_libraryHostRef()
