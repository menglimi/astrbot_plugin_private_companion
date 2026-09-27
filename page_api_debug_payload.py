# -*- coding: utf-8 -*-
"""debug_payload 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（2 个方法 + 1 个模块级名字 + 0 个类级赋值 / 97 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any



_DEBUG_TAIL_MAX_WINDOW_BYTES = 16 * 1024 * 1024


class PrivateCompanionPageApiDebugPayloadMixin:
    """debug_payload 域（从 PrivateCompanionPageApi 拆出）。"""


    @staticmethod
    def _read_debug_lines(path: Path, *, tail_lines: int | None = None) -> list[str]:
        """Read a bounded tail for collapsed status requests."""
        if tail_lines is None:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        limit = max(1, min(4096, int(tail_lines)))
        # 从文件尾往前读：先读一个小窗口，只有窗口里没有换行（说明落进了超长行）
        # 才按指数放大，避免在正常日志上把整个文件读进内存。
        chunk_size = max(64 * 1024, limit * 2048)
        max_window = min(_DEBUG_TAIL_MAX_WINDOW_BYTES, 16 * 1024 * 1024)
        content = b""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            window_start = end
            found_newline = False
            while window_start > 0:
                window_start = max(0, window_start - chunk_size)
                handle.seek(window_start)
                window = handle.read(end - window_start)
                if b"\n" in window and (window_start == 0 or window.count(b"\n") > limit):
                    content = window
                    found_newline = True
                    break
                if window_start == 0:
                    # 整个文件都在同一段缓冲里（含只有一条超长记录的极端情况）。
                    content = window
                    found_newline = b"\n" in window
                    break
                if end - window_start >= max_window:
                    # 命中硬上限：保留窗口内最新的一段，绝不把 tail 读成空白。
                    content = window
                    found_newline = b"\n" in window
                    break
                chunk_size = min(max_window, chunk_size * 2)
        if window_start > 0 and found_newline:
            # 窗口起点落在某条记录内部：丢掉开头那条不完整记录。
            content = content[content.find(b"\n") + 1:]
        return content.decode("utf-8", "replace").splitlines()[-limit:]

    @staticmethod
    def _attach_debug_payload_contents(
        events: list[dict[str, Any]],
        roots: list[Path],
        *,
        max_total_bytes: int = 2 * 1024 * 1024,
        max_payload_bytes: int = 512 * 1024,
    ) -> None:
        """Attach captured sidecar bodies for the explicitly expanded view.

        Payload paths are recorder-generated relative paths. Resolve them only
        below each known ``photo_debug`` directory and reject symlinks so a
        forged log line cannot turn the page API into an arbitrary file reader.
        """
        remaining = max(0, int(max_total_bytes))
        if remaining <= 0:
            return
        for event in events:
            if remaining <= 0 or not isinstance(event, dict):
                break
            data = event.get("data")
            payloads = data.get("payloads") if isinstance(data, dict) else None
            if not isinstance(payloads, dict):
                continue
            for metadata in payloads.values():
                if remaining <= 0 or not isinstance(metadata, dict):
                    continue
                relative = str(metadata.get("path") or "").strip()
                if not relative or not bool(metadata.get("captured")):
                    continue
                for root in roots:
                    debug_root = root / "photo_debug"
                    candidate = debug_root / relative
                    try:
                        if candidate.is_symlink():
                            continue
                        resolved_root = debug_root.resolve(strict=False)
                        resolved = candidate.resolve(strict=True)
                        if resolved == resolved_root or resolved_root not in resolved.parents:
                            continue
                        if not resolved.is_file():
                            continue
                        size = resolved.stat().st_size
                        if size > min(max_payload_bytes, remaining):
                            metadata["content_truncated"] = True
                            metadata["content_limit"] = min(max_payload_bytes, remaining)
                            break
                        raw = resolved.read_bytes()
                    except (OSError, RuntimeError, ValueError):
                        continue
                    remaining -= len(raw)
                    encoding = str(metadata.get("encoding") or "utf-8").lower()
                    if encoding in {"base64", "binary"} or str(metadata.get("mime_type") or "").startswith("image/"):
                        metadata["content_base64"] = base64.b64encode(raw).decode("ascii")
                    else:
                        metadata["content"] = raw.decode("utf-8", "replace")
                    break
