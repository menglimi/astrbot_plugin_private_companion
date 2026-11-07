"""Freeze document locators and bind extracted text to the original message body."""

import asyncio
from dataclasses import dataclass
import json
import logging
import re

from companion_v2.execution.models import TaskError
from companion_v2.conversation.media.understanding import (
    DOCUMENT_SUFFIXES,
    TEXT_SUFFIXES,
    media_scope_enabled,
)


logger = logging.getLogger(__name__)
MAX_FILES = 4


def safe_name(value):
    if type(value) is not str:
        return "附件"
    return re.sub(r"[\x00-\x1f\x7f]", "", value.replace("\\", "/").rsplit("/", 1)[-1])[:200] or "附件"


@dataclass(frozen=True)
class NativeFileLocator:
    position: int
    name: str
    locator: str
    # Set only for an attachment the trusted host already wrote to its temp
    # directory (Telegram documents are materialized there before intake).
    host_resolved: bool = False


@dataclass(frozen=True)
class FileText:
    position: int
    name: str
    content_hash: str
    byte_count: int
    text: str
    truncated: bool

    def __post_init__(self):
        if (type(self.position) is not int or not 0 <= self.position < 128
                or type(self.name) is not str or self.name != safe_name(self.name)
                or type(self.content_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.content_hash)
                or type(self.byte_count) is not int or not 0 < self.byte_count <= 16 * 1024 * 1024
                or type(self.text) is not str or not 0 < len(self.text) <= 48000
                or type(self.truncated) is not bool):
            raise TaskError("host_file_invalid", "document extraction must retain bounded original attribution")


def file_locator(position, part):
    value = getattr(part, "url", None) or getattr(part, "file_", None) or ""
    value = value if type(value) is str else ""
    from .host_paths import is_host_temp_file
    return NativeFileLocator(position, safe_name(getattr(part, "name", None)), value,
                             host_resolved=is_host_temp_file(value))


async def prepare_file_text(locators, loader, preferences, *, client, scope, payload_budget=65536):
    remaining = preferences["file_understanding_max_chars"]
    deadline = asyncio.get_running_loop().time() + preferences["file_understanding_timeout_seconds"]
    result = {}
    errors = {"host_file_limit": "文件超过读取大小上限", "host_file_format_unsupported": "文件格式暂不支持",
              "host_file_empty": "没有提取到可读文字，可能需要 OCR 或文件已加密",
              "host_file_decoder_unavailable": "文件解析组件暂不可用"}
    for ordinal, locator in enumerate(locators):
        entry = {"name": locator.name, "component_position": locator.position}
        if not preferences["file_understanding_enabled"]:
            entry["status"] = "文件理解未启用，未读取正文"
        elif not media_scope_enabled(preferences, "file_understanding", scope):
            entry["status"] = "当前会话范围未启用文件理解，未读取正文"
        elif ordinal >= MAX_FILES or remaining < 1:
            entry["status"] = "本轮文件读取上限已达到，未读取此附件"
        else:
            try:
                value = await asyncio.wait_for(loader(locator, client=client, scope=scope,
                    maximum_bytes=preferences["file_understanding_max_size_mb"] * 1024 * 1024,
                    maximum_chars=remaining, formats=preferences["file_understanding_allowed_suffixes"]),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()))
                if (type(value) is not FileText or value.position != locator.position
                        or value.name != locator.name or len(value.text) > remaining):
                    raise TaskError("host_file_invalid", "document loader changed the frozen attachment")
                entry.update(sha256=value.content_hash, byte_count=value.byte_count, text=value.text,
                             coverage="部分正文" if value.truncated else "已提取正文")
                remaining -= len(value.text)
            except TimeoutError:
                entry["status"] = "文件读取超时，未取得正文"
                logger.warning("[CompanionV2] 文件理解未完成 position=%s reason=host_file_timeout", locator.position)
            except Exception as error:
                code = error.code if isinstance(error, TaskError) else "host_file_failed"
                if code == "host_file_decoder_unavailable":
                    # 点名是哪种格式缺组件：泛化的「暂不可用」会让人以为是文件坏了。
                    suffix = locator.name.rsplit(".", 1)[-1].upper() if "." in locator.name else "该格式"
                    entry["status"] = f"{suffix} 解析组件未安装，未读取正文"
                else:
                    entry["status"] = errors.get(code, "文件读取失败，未取得正文")
                logger.warning("[CompanionV2] 文件理解未完成 position=%s reason=%s", locator.position, code)
        payload = "\n[文件阅读材料]\n" + json.dumps(entry, ensure_ascii=False) + "\n"
        while len(payload) > payload_budget and entry.get("text"):
            entry["text"] = entry["text"][:len(entry["text"]) // 2]
            entry["coverage"] = "部分正文"
            payload = "\n[文件阅读材料]\n" + json.dumps(entry, ensure_ascii=False) + "\n"
        if len(payload) <= payload_budget:
            result[locator.position] = payload
            payload_budget -= len(payload)
    return result
