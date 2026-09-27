# -*- coding: utf-8 -*-
"""message_display 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 63 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
from .helpers import _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from typing import Any



class PrivateCompanionPageApiMessageDisplayMixin:
    """message_display 域（从 PrivateCompanionPageApi 拆出）。"""


    def _display_message_text(self, value: Any, limit: int = 500) -> str:
        source = str(value or "").strip()
        source = source.strip("\"'“”‘’` ")
        if self._looks_like_internal_delivery_receipt(source):
            return ""
        if re.fullmatch(r"[.。…~～\s\"'“”‘’`-]{0,12}", source):
            return ""
        if re.search(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", source, flags=re.IGNORECASE | re.DOTALL):
            outside = re.sub(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", "", source, flags=re.IGNORECASE | re.DOTALL)
            outside = re.sub(r"</?t{2,}s\b[^>]*>", "", outside, flags=re.IGNORECASE).strip()
            if re.search(r"[\u4e00-\u9fff]", outside):
                source = outside
            else:
                source = re.sub(r"</?t{2,}s\b[^>]*>", "", source, flags=re.IGNORECASE)
        if re.search(r"[\u3040-\u30ff]", source) and re.search(r"[\u4e00-\u9fff]", source):
            units = re.findall(r".*?[。！？!?…~～]+|.+$", source, flags=re.DOTALL)
            kept = [unit.strip() for unit in units if unit.strip() and not re.search(r"[\u3040-\u30ff]", unit)]
            if kept and any(re.search(r"[\u4e00-\u9fff]", item) for item in kept):
                source = "".join(kept)
        return self._single_line(_strip_internal_message_blocks(source, enabled=bool(runtime_persona_setting(self.plugin, "enable_framework_error_leak_guard", True))), limit)

    @staticmethod
    def _looks_like_internal_delivery_receipt(text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if compact in {"已发送", "发送成功", "发送完成", "发送完毕", "已成功发送", "消息已发送", "消息发送成功"}:
            return True
        markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in markers):
            return True
        return (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        )

    def _sanitize_last_bot_interjection(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return {}
        item = dict(value)
        item["text"] = self._display_message_text(item.get("text"), 120)
        if not item["text"] and not item.get("has_image"):
            return {}
        return item

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds or 0))
        if seconds < 60:
            return "不到 1 分钟"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        rest = minutes % 60
        return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"
