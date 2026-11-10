"""V2-owned, one-shot local desktop capture with durable artifact receipts."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from .screen_source import ScreenSourceSettings, capture_remote


SCHEMA = "companion.native-provider.v1"
PLUGIN_ID = "astrbot_plugin_private_companion.screen"
ACTION = "screen.capture"
MAX_BYTES = 5 * 1024 * 1024


def screen_package_hash():
    root = Path(__file__).parent
    return hashlib.sha256(b"".join((root / name).read_bytes()
        for name in ("screen_provider.py", "screen_source.py"))).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def capture_local_screen():
    from PIL import ImageGrab

    image = ImageGrab.grab(all_screens=False).convert("RGB")
    image.thumbnail((2048, 2048))
    body = io.BytesIO()
    image.save(body, format="JPEG", quality=80, optimize=True)
    content = body.getvalue()
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("screen_capture_size")
    return content


class NativeScreenProvider:
    def __init__(self, root: Path, *, version: str, capture=capture_local_screen, now=None):
        self.version = self._required(version, "version")
        self.capture = capture
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "operations.sqlite3"
        self.source = ScreenSourceSettings(self.root)
        self._lock = asyncio.Lock()
        self._closed = False
        self.artifact_hash = screen_package_hash()
        self.generation = _hash(_json([self.artifact_hash, self.version]).encode("utf-8"))
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS operations (
                invocation_id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                state TEXT NOT NULL, result_json TEXT, content BLOB)""")

    @staticmethod
    def _required(value, field):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("native Screen requires a version")
        return value.strip()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def manifest(self):
        if self._closed:
            raise ValueError("native Screen provider is closed")
        return {"schema": SCHEMA, "plugin_id": PLUGIN_ID, "provider_id": "screen.native",
                "generation": self.generation,
                "package": {"extension_id": "companion.screen.native", "package_id": PLUGIN_ID,
                            "publisher_id": "private-companion-v2", "package_version": self.version,
                            "artifact_hash": self.artifact_hash,
                            "capabilities": ["screen.read", "screen.process"],
                            "dataset_owners": ["screen"]},
                "tools": [{"action": ACTION, "capability": "screen.process",
                           "source_owner": "screen", "description":
                           "按需采集一次已配置设备的桌面屏幕，返回当前画面供本轮观察；仅在当前问题需要看屏幕时调用。",
                           "parameters": {"type": "object", "properties": {},
                                          "required": [], "additionalProperties": False},
                           "side_effect": "external_device", "timeout_seconds": 30}]}

    def _request(self, request):
        if self._closed or type(request) is not dict or request.get("schema") != SCHEMA:
            raise ValueError("screen_native_request_invalid")
        if request.get("action") != ACTION or request.get("arguments") != {}:
            raise ValueError("screen_native_action_invalid")
        binding = request.get("binding")
        if (type(binding) is not dict or binding.get("provider") != "screen.native"
                or binding.get("capability") != "screen.process"
                or binding.get("version") != self.version or binding.get("generation") != self.generation):
            raise ValueError("screen_native_binding_changed")
        context = request.get("context")
        scope = context.get("audience", {}).get("scope", {}) if type(context) is dict else {}
        # 会话范围不是权限边界：V2 已在调用前按主体完成授权，这里只校验请求完整性。
        if (type(scope) is not dict
                or not all(isinstance(request.get(key), str) and request[key]
                           for key in ("invocation_id", "origin_key", "arguments_hash"))):
            raise ValueError("screen_native_context_invalid")
        return context

    @staticmethod
    def _result(request, state, *, result=None, content=None):
        invocation = request["invocation_id"]
        operation = "screen-native-" + _hash(invocation.encode("utf-8"))
        successful = state == "succeeded"
        artifact = ({"artifact_id": operation + "-image", "operation_id": operation,
                     "content_hash": _hash(content), "mime_type": "image/jpeg",
                     "size_bytes": len(content)} if successful else None)
        return {"schema": SCHEMA, "invocation_id": invocation,
                "origin_key": request["origin_key"], "arguments_hash": request["arguments_hash"],
                "binding": request["binding"], "state": state,
                "effect": "committed" if successful else "not_committed" if state == "failed" else "unknown",
                "operation_id": operation, "receipt_ref": operation,
                "result": result if successful else None,
                "artifacts": [artifact] if artifact else []}

    async def execute(self, request, *, before_dispatch):
        self._request(request)
        original = _json(request)
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute("SELECT request_json, state, result_json FROM operations WHERE invocation_id = ?",
                                         (request["invocation_id"],)).fetchone()
                if row is not None:
                    if row[0] != original:
                        raise ValueError("screen_native_original_changed")
                    if row[1] == "succeeded":
                        return json.loads(row[2])
                    return self._result(request, "unknown")
                connection.execute("INSERT INTO operations VALUES (?, ?, 'pending', NULL, NULL)",
                                   (request["invocation_id"], original))
            try:
                # Pin the source before admission; failures never select another device.
                settings = self.source.read()
                before_dispatch()
                remote = (await asyncio.to_thread(capture_remote, settings, request["invocation_id"], now=self.now())
                          if settings["mode"] == "remote" else None)
                content = remote.content if remote is not None else await asyncio.to_thread(self.capture)
                if type(content) is not bytes or not 0 < len(content) <= MAX_BYTES:
                    raise ValueError("screen_native_image_invalid")
                captured_at = remote.captured_at if remote is not None else self.now().astimezone(timezone.utc).isoformat()
                result = self._result(request, "succeeded", content=content,
                    result={"device_id": remote.device_id if remote is not None else "local-desktop",
                            "surface": remote.surface if remote is not None else "desktop",
                            "captured_at": captured_at, "clock_quality": "client_reported",
                            "window_title": remote.window_title if remote is not None else None,
                            "gaps": remote.gaps if remote is not None else
                                ["other displays or obscured areas may be unseen"]})
                with self._connect() as connection:
                    connection.execute("UPDATE operations SET state='succeeded', result_json=?, content=? WHERE invocation_id=?",
                                       (_json(result), content, request["invocation_id"]))
                return result
            except asyncio.CancelledError:
                raise
            except Exception:
                with self._connect() as connection:
                    connection.execute("UPDATE operations SET state='unknown' WHERE invocation_id=?",
                                       (request["invocation_id"],))
                return self._result(request, "unknown")

    async def read_artifact(self, request, *, before_dispatch):
        self._request(request)
        before_dispatch()
        original = {key: value for key, value in request.items()
                    if key not in ("operation_id", "artifact_id", "content_hash")}
        with self._connect() as connection:
            row = connection.execute("SELECT request_json, state, result_json, content FROM operations WHERE invocation_id=?",
                                     (request["invocation_id"],)).fetchone()
        if row is None or row[0] != _json(original) or row[1] != "succeeded":
            raise ValueError("screen_native_artifact_unavailable")
        result = json.loads(row[2])
        artifact = result["artifacts"][0]
        if (request.get("operation_id"), request.get("artifact_id"), request.get("content_hash")) != (
                artifact["operation_id"], artifact["artifact_id"], artifact["content_hash"]):
            raise ValueError("screen_native_artifact_changed")
        content = bytes(row[3])
        if _hash(content) != artifact["content_hash"]:
            raise ValueError("screen_native_artifact_changed")
        before_dispatch()
        return content

    def close(self):
        self._closed = True
