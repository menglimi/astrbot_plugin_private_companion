"""Run in the logged-in desktop session; return screenshots only, never chat or schedule."""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import OrderedDict
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit
from uuid import uuid4


PROTOCOL = "companion.screen-capture.v1"
MAX_BYTES = 5 * 1024 * 1024
log = logging.getLogger("companion.screen.collector")


async def portal_image():
    """Use the desktop's own consent dialog on GNOME/KDE Wayland."""
    from dbus_next.aio import MessageBus
    from dbus_next import Variant
    bus = await MessageBus().connect()
    handle = "companion_" + uuid4().hex
    sender = bus.unique_name.lstrip(":").replace(".", "_")
    expected = "/org/freedesktop/portal/desktop/request/" + sender + "/" + handle
    future = asyncio.get_running_loop().create_future()

    def response(message):
        if (message.path == expected and message.interface == "org.freedesktop.portal.Request"
                and message.member == "Response" and not future.done()):
            code, result = message.body
            if code != 0 or "uri" not in result:
                future.set_exception(ValueError("desktop screenshot consent was not granted"))
            else:
                future.set_result(result["uri"].value)

    bus.add_message_handler(response)
    try:
        # Subscribe before requesting: an immediate response must not be lost.
        from dbus_next import Message
        await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s",
            body=["type='signal',interface='org.freedesktop.portal.Request',member='Response',path='" + expected + "'"]))
        tree = await bus.introspect("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
        proxy = bus.get_proxy_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", tree)
        method = proxy.get_interface("org.freedesktop.portal.Screenshot")
        actual = await method.call_screenshot("", {"handle_token": Variant("s", handle),
                                                   "interactive": Variant("b", False)})
        if actual != expected:
            raise ValueError("desktop screenshot portal returned an unexpected handle")
        uri = urlsplit(await asyncio.wait_for(future, 12))
        if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
            raise ValueError("desktop screenshot portal returned an invalid file")
        from PIL import Image
        with Image.open(unquote(uri.path)) as image:
            return image.copy()
    finally:
        bus.remove_message_handler(response)
        bus.disconnect()


def capture_desktop(config):
    from PIL import Image, ImageGrab
    backend = config.get("backend", "auto")
    wayland = sys.platform == "linux" and bool(os.environ.get("WAYLAND_DISPLAY"))
    if backend == "auto":
        # niri supports grim's screencopy protocol. It captures directly without
        # opening a picker or replacing the desktop clipboard.
        niri = bool(os.environ.get("NIRI_SOCKET")) or "niri" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        backend = "grim" if wayland and niri else "portal" if wayland else "pillow"
    if backend == "portal":
        image = asyncio.run(portal_image())
    elif backend == "grim":
        # A fixed executable and fixed arguments; the HTTP caller cannot issue commands.
        result = subprocess.run(["grim", "-"], check=True, capture_output=True, timeout=12)
        with Image.open(io.BytesIO(result.stdout)) as opened:
            image = opened.copy()
    elif backend == "pillow":
        if wayland:
            raise ValueError("select the desktop portal or grim for Wayland")
        image = ImageGrab.grab(all_screens=config.get("all_screens", False))
    else:
        raise ValueError("unknown screenshot backend")
    image = image.convert("RGB")
    image.thumbnail((2048, 2048))
    body = io.BytesIO()
    image.save(body, format="JPEG", quality=80, optimize=True)
    content = body.getvalue()
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("screenshot size is invalid")
    return content


class Collector:
    def __init__(self, config, capture=capture_desktop, now=None):
        if (type(config) is not dict or type(config.get("token")) is not str
                or not 32 <= len(config["token"]) <= 512
                or type(config.get("device_id")) is not str or not config["device_id"].strip()):
            raise ValueError("configure a collector credential and device identity")
        self.config, self.capture = dict(config), capture
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.results = OrderedDict()

    def collect(self, command):
        if (type(command) is not dict or set(command) != {"schema", "request_id", "device_id"}
                or command["schema"] != PROTOCOL or command["device_id"] != self.config["device_id"]
                or type(command["request_id"]) is not str or not 1 <= len(command["request_id"]) <= 256):
            raise ValueError("capture request does not match this device")
        request_id = command["request_id"]
        with self.lock:
            if request_id in self.results:
                code, body = self.results[request_id]
                return code, body
            try:
                content = self.capture(self.config)
                body = {"schema": PROTOCOL, "request_id": request_id,
                        "device_id": self.config["device_id"], "surface": "desktop",
                        "captured_at": self.now().astimezone(timezone.utc).isoformat(), "window_title": None,
                        "gaps": ["other displays or obscured areas may be unseen"],
                        "image_base64": base64.b64encode(content).decode("ascii")}
                code = 200
                log.info("screen captured device=%s bytes=%d", self.config["device_id"], len(content))
            except Exception as error:
                code, body = 503, {"error": "screen_capture_unavailable"}
                log.warning("screen capture failed device=%s error_type=%s",
                            self.config["device_id"], type(error).__name__)
            # Both success and failure are replayed. A repeated request never
            # silently creates a second observation after an uncertain result.
            self.results[request_id] = (code, body)
            while len(self.results) > 8:
                self.results.popitem(last=False)
            return code, body


def make_server(config, *, capture=capture_desktop, now=None):
    collector = Collector(config, capture, now)

    class Handler(BaseHTTPRequestHandler):
        server_version = "CompanionScreen/1"

        def log_message(self, *_):
            pass

        def reply(self, code, body):
            content = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(content)

        def authorized(self):
            return (not self.headers.get("Origin") and hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + config["token"]))

        def do_GET(self):
            if not self.authorized():
                return self.reply(403, {"error": "forbidden"})
            if self.path != "/health":
                return self.reply(404, {"error": "not_found"})
            self.reply(200, {"status": "ready", "device_id": config["device_id"]})

        def do_POST(self):
            self.connection.settimeout(5)
            if not self.authorized():
                return self.reply(403, {"error": "forbidden"})
            if self.path != "/capture":
                return self.reply(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (not 0 < length <= 4096 or self.headers.get("Transfer-Encoding")
                        or self.headers.get_content_type() != "application/json"):
                    raise ValueError("invalid capture body")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("incomplete capture body")
                command = json.loads(body)
                code, result = collector.collect(command)
            except (ValueError, TypeError, UnicodeError, OSError):
                return self.reply(400, {"error": "invalid_request"})
            self.reply(code, result)

    server = ThreadingHTTPServer((config.get("listen", "127.0.0.1"), config.get("port", 6191)), Handler)
    server.daemon_threads = True
    server.collector = collector
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--init", action="store_true", help="create a private config; never overwrite an existing file")
    parser.add_argument("--device-id", default="desktop")
    args = parser.parse_args()
    if args.init:
        args.config.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"listen": "127.0.0.1", "port": 6191, "device_id": args.device_id,
                       "token": secrets.token_urlsafe(48), "backend": "auto", "all_screens": False},
                      stream, ensure_ascii=False, indent=2)
        print("Collector configuration created; keep its credential private.")
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logging.basicConfig(filename=args.config.with_suffix(".log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(config)
    log.info("collector listening address=%s port=%s", *server.server_address)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
