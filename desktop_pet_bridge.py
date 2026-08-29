# -*- coding: utf-8 -*-
"""Optional local bridge for mirroring confirmed proactive text to the desktop pet."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from .logging_util import get_module_logger

logger = get_module_logger(__name__)



def _clean_text(value: Any, limit: int = 4000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


class DesktopPetBridge:
    """Best-effort sender that never becomes part of platform delivery success."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_failure_at = 0.0
        self._last_status = "等待桌宠"
        self._last_error = ""
        self._sent_count = 0
        self._failure_log_cooldown_seconds = 60.0

    def _enabled(self) -> bool:
        return bool(getattr(self.owner, "enable_desktop_pet_bridge", True))

    def _url(self) -> str:
        value = str(
            getattr(self.owner, "desktop_pet_bridge_url", "http://127.0.0.1:18120")
            or "http://127.0.0.1:18120"
        ).strip()
        if value.endswith("/v1/say"):
            return value
        return f"{value.rstrip('/')}/v1/say"

    def _duration_ms(self) -> int:
        try:
            value = int(getattr(self.owner, "desktop_pet_bridge_duration_ms", 6000))
        except (TypeError, ValueError):
            value = 6000
        return max(1500, min(30000, value))

    def _timeout_seconds(self) -> float:
        try:
            value = int(getattr(self.owner, "desktop_pet_bridge_timeout_ms", 800))
        except (TypeError, ValueError):
            value = 800
        return max(0.2, min(3.0, value / 1000.0))

    def enqueue(
        self,
        text: Any,
        *,
        source: str = "private_companion",
        kind: str = "proactive",
        speaker: str = "",
        reason: str = "",
        action: str = "message",
    ) -> bool:
        content = _clean_text(text)
        if not content or not self._enabled():
            return False
        try:
            task = asyncio.create_task(
                self._send(
                    content,
                    source=_clean_text(source, 80) or "private_companion",
                    kind=_clean_text(kind, 40) or "proactive",
                    speaker=_clean_text(speaker, 80),
                    reason=_clean_text(reason, 120),
                    action=_clean_text(action, 80) or "message",
                ),
                name="private-companion-desktop-pet-message",
            )
        except RuntimeError:
            return False
        self._tasks.add(task)
        task.add_done_callback(self._discard_task)
        return True

    def _discard_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            return

    async def _send(
        self,
        text: str,
        *,
        source: str,
        kind: str,
        speaker: str,
        reason: str,
        action: str,
    ) -> None:
        now = time.monotonic()
        if self._last_failure_at and now - self._last_failure_at < self._failure_log_cooldown_seconds:
            return
        payload = {
            "text": text,
            "source": source,
            "kind": kind,
            "speaker": speaker or str(getattr(self.owner, "bot_name", "") or "").strip(),
            "reason": reason,
            "action": action,
            "durationMs": self._duration_ms(),
            "messageId": f"companion-{uuid.uuid4().hex}",
        }
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._post_json, payload),
                timeout=self._timeout_seconds() + 0.2,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_failure_at = time.monotonic()
            self._last_status = "桌宠未连接"
            self._last_error = str(exc)[:240]
            logger.debug(
                "桌宠消息镜像跳过（桌宠未运行或接口不可用）: %s",
                self._last_error,
            )
            return
        self._last_failure_at = 0.0
        self._last_status = "已连接"
        self._last_error = ""
        self._sent_count += 1

    def _post_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._url(),
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "PrivateCompanion-DesktopPetBridge/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds()) as response:
            if not 200 <= int(getattr(response, "status", 200)) < 300:
                raise urllib.error.HTTPError(
                    self._url(),
                    int(response.status),
                    "桌宠接口返回失败状态",
                    response.headers,
                    None,
                )

    async def stop(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "url": self._url(),
            "status": self._last_status,
            "last_error": self._last_error,
            "sent_count": self._sent_count,
            "pending": sum(1 for task in self._tasks if not task.done()),
        }
