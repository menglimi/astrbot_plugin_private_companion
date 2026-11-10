"""The Screen device source; remote transport never schedules or interprets captures."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
from urllib import request, parse
from uuid import uuid4


PROTOCOL = "companion.screen-capture.v1"
MAX_BYTES = 5 * 1024 * 1024
MAX_RESPONSE = (MAX_BYTES + 2) // 3 * 4 + 8192
DEFAULTS = {"mode": "local", "remote_url": "", "remote_token": "",
            "device_id": "", "timeout_seconds": 10}


def unique_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate screen field")
            result[key] = value
        return result
    return json.loads(body, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite screen field")))


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ValueError("invalid screen source fields")
    if values["mode"] not in ("local", "remote"):
        raise ValueError("invalid screen source mode")
    for key in ("remote_url", "remote_token", "device_id"):
        if type(values[key]) is not str or len(values[key]) > 2048 or any(ord(c) < 32 for c in values[key]):
            raise ValueError("invalid screen source text")
    if type(values["timeout_seconds"]) is not int or not 1 <= values["timeout_seconds"] <= 20:
        raise ValueError("invalid screen source timeout")
    if values["mode"] == "remote":
        url = parse.urlsplit(values["remote_url"])
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path != "/capture"):
            raise ValueError("screen source requires an exact capture endpoint")
        if url.scheme == "http" and url.hostname != "localhost":
            address = ipaddress.ip_address(url.hostname)
            if not (address.is_private or address.is_loopback
                    or address in ipaddress.ip_network("100.64.0.0/10")) or address.is_unspecified:
                raise ValueError("remote screen requires HTTPS outside private networks")
        if not 32 <= len(values["remote_token"]) <= 512 or not values["device_id"].strip():
            raise ValueError("screen source requires a device and a collector credential")
    return dict(values)


class ScreenSourceSettings:
    """Installation-wide device selection, independent of per-window consent."""

    def __init__(self, root):
        self.path = Path(root) / "capture-source.json"

    def read(self):
        try:
            saved = unique_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {}
        if type(saved) is not dict or set(saved) - set(DEFAULTS):
            raise ValueError("invalid saved screen source")
        return validate({**DEFAULTS, **saved})

    def snapshot(self):
        values = self.read()
        revision = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        public = {key: value for key, value in values.items() if key != "remote_token"}
        return {"settings": public, "token_configured": bool(values["remote_token"]), "revision": revision}

    def update(self, updates, revision):
        if type(updates) is not dict or set(updates) - set(DEFAULTS):
            raise ValueError("invalid screen source update")
        if type(revision) is not str or revision != self.snapshot()["revision"]:
            raise ValueError("screen source changed; reload before saving")
        changes = {key: value for key, value in updates.items() if key != "remote_token" or value != ""}
        values = validate({**self.read(), **changes})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(".capture-source-" + uuid4().hex)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(values, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.snapshot()


@dataclass(frozen=True)
class RemoteScreen:
    content: bytes
    device_id: str
    surface: str
    captured_at: str
    window_title: str | None
    gaps: list[str]


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("screen collector redirects are not allowed")


def capture_remote(settings, invocation_id, *, now=None):
    """One authenticated request; no proxy, redirect, retry or local fallback."""
    settings = validate(settings)
    payload = json.dumps({"schema": PROTOCOL, "request_id": invocation_id,
                          "device_id": settings["device_id"]}).encode()
    outgoing = request.Request(settings["remote_url"], data=payload, method="POST",
        headers={"Authorization": "Bearer " + settings["remote_token"],
                 "Content-Type": "application/json", "Accept": "application/json"})
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    with opener.open(outgoing, timeout=settings["timeout_seconds"]) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise ValueError("screen collector response is unavailable")
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError("screen collector response is too large")
    result = unique_json(body)
    if (type(result) is not dict or set(result) != {"schema", "request_id", "device_id", "surface",
            "captured_at", "window_title", "gaps", "image_base64"}
            or result["schema"] != PROTOCOL or result["request_id"] != invocation_id
            or result["device_id"] != settings["device_id"]):
        raise ValueError("screen collector response does not match the requested device")
    if (type(result["surface"]) is not str or not 1 <= len(result["surface"]) <= 256
            or result["window_title"] is not None and
                (type(result["window_title"]) is not str or len(result["window_title"]) > 1024)
            or type(result["gaps"]) is not list or len(result["gaps"]) > 16
            or any(type(gap) is not str or len(gap) > 512 for gap in result["gaps"])):
        raise ValueError("screen collector coverage is invalid")
    captured = datetime.fromisoformat(result["captured_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if captured.tzinfo is None or not -30 <= (current - captured).total_seconds() <= 60:
        raise ValueError("screen collector returned an old or invalid capture time")
    content = base64.b64decode(result["image_base64"], validate=True)
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("screen collector image size is invalid")
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "JPEG" or max(image.size) > 4096 or min(image.size) < 1:
            raise ValueError("screen collector must return a bounded JPEG")
        image.verify()
    return RemoteScreen(content, result["device_id"], result["surface"],
                        captured.astimezone(timezone.utc).isoformat(), result["window_title"], result["gaps"])
