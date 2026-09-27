# -*- coding: utf-8 -*-
"""normalize_text 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（14 个方法 + 1 个模块级名字 + 0 个类级赋值 / 143 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
import time
from .helpers import _text_looks_garbled
from typing import Any



class _PageApiError(dict[str, Any]):
    """Dictionary-compatible API error with HTTP-only status metadata."""

    __slots__ = ("http_status",)

    def __init__(self, payload: dict[str, Any], http_status: int) -> None:
        super().__init__(payload)
        self.http_status = int(http_status)


class PrivateCompanionPageApiNormalizeTextMixin:
    """normalize_text 域（从 PrivateCompanionPageApi 拆出）。"""


    @staticmethod
    def _extract_labeled_text(text: str, label: str, limit: int) -> str:
        source = str(text or "")
        pattern = rf"{re.escape(label)}\s*[：:]\s*(.+?)(?=(?:\s+[^\s：:]{{2,20}}\s*[：:])|$)"
        match = re.search(pattern, source)
        if not match:
            return ""
        return PrivateCompanionPageApiNormalizeTextMixin._single_line(match.group(1), limit)

    @staticmethod
    def _normalize_id_list(value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("\n", ",").split(",")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = PrivateCompanionPageApiNormalizeTextMixin._single_line(item, 128)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @classmethod
    def _dedupe_text_list(cls, value: Any, limit: int = 12) -> list[str]:
        items = value if isinstance(value, list) else []
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = cls._single_line(item, 180)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _normalize_private_target_id_list(self, value: Any) -> list[str]:
        normalizer = getattr(self.plugin, "_normalize_private_identity_id", None)
        result: list[str] = []
        seen: set[str] = set()
        for item in self._normalize_id_list(value):
            text = normalizer(item) if callable(normalizer) else self._single_line(item, 128)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit]

    @classmethod
    def _sanitize_news_text(cls, value: Any, limit: int, *, fallback: str = "") -> str:
        text = cls._single_line(value, limit)
        if text and _text_looks_garbled(text):
            return fallback
        return text

    @staticmethod
    def _multi_line(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit].strip()

    @staticmethod
    def _multi_line_head_tail(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) <= limit:
            return text.strip()
        marker = "\n\n（中间内容过长，已保留开头和结尾；请优先依据稳定重复信息归纳。）\n\n"
        if limit <= len(marker) + 200:
            return text[:limit].strip()
        head_len = max(200, int((limit - len(marker)) * 0.65))
        tail_len = max(200, limit - len(marker) - head_len)
        return (text[:head_len].strip() + marker + text[-tail_len:].strip())[:limit].strip()

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return {"success": True, "data": data, "ts": int(time.time())}

    @staticmethod
    def _is_http_error_response(value: Any) -> bool:
        return (
            isinstance(value, _PageApiError)
            or (isinstance(value, dict) and value.get("success") is False)
            or (
                isinstance(value, tuple)
                and len(value) == 2
                and isinstance(value[0], dict)
                and isinstance(value[1], int)
            )
        )

    @staticmethod
    def _as_http_response(value: Any) -> Any:
        if isinstance(value, _PageApiError):
            return dict(value), value.http_status
        if isinstance(value, dict) and value.get("success") is False:
            return value, 400
        return value

    @staticmethod
    def _safe_error_message(message: Any) -> str:
        text = PrivateCompanionPageApiNormalizeTextMixin._single_line(message, 220)
        if not text:
            return "请求失败"
        lowered = text.lower()
        sensitive_markers = (
            "api_key",
            "apikey",
            "password",
            "authorization",
            "cookie",
            "credential",
            "secret",
            "token",
        )
        if any(marker in lowered for marker in sensitive_markers):
            return "内部操作失败"
        if "\\\\" in text or re.search(r"(?:[A-Za-z]:[\\/]|(?:^|[\\s'\"=])/[^\s]+)", text):
            return "内部操作失败"
        return text

    @staticmethod
    def _error(message: str, *, status_code: int = 400) -> dict[str, Any]:
        return _PageApiError(
            {
                "success": False,
                "error": PrivateCompanionPageApiNormalizeTextMixin._safe_error_message(message),
                "ts": int(time.time()),
            },
            status_code,
        )

    @staticmethod
    def _exception_error(message: str = "内部操作失败") -> dict[str, Any]:
        return PrivateCompanionPageApiNormalizeTextMixin._error(message, status_code=500)
