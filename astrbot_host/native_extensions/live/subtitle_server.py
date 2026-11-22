"""
Transparent browser overlay for typewriter subtitles.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from astrbot.api import logger

MAX_AUDIO_ENTRIES = 10


class SubtitleServer:
    def __init__(self, host: str, port: int, style: dict[str, Any]):
        self.host = host
        self.port = int(port)
        self.style = style
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._last_payload: dict[str, Any] | None = None
        self._audio_files: OrderedDict[str, str] = OrderedDict()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def start(self) -> None:
        if self._runner:
            return
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/show", self._handle_show)
        app.router.add_post("/show", self._handle_show)
        app.router.add_get("/clear", self._handle_clear)
        app.router.add_get("/audio/{token}", self._handle_audio)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except BaseException:
            try:
                await runner.cleanup()
            except Exception:
                pass
            self._app = None
            self._runner = None
            self._site = None
            raise
        self._app = app
        self._runner = runner
        self._site = site
        logger.info(f"[字幕] 字幕 overlay 已启动: {self.url}")

    async def stop(self) -> None:
        clients = list(self._clients)
        self._clients.clear()
        runner = self._runner
        self._app = None
        self._runner = None
        self._site = None
        self._audio_files.clear()
        try:
            for ws in clients:
                try:
                    await ws.close()
                except Exception as exc:
                    logger.debug(f"[字幕] 关闭 overlay WebSocket 失败: {exc}")
        finally:
            if runner:
                await runner.cleanup()
        logger.info("[字幕] 字幕 overlay 已停止")

    async def show(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        payload = {"type": "subtitle", "text": text, "style": self.style}
        self._last_payload = payload
        await self._broadcast(payload)

    async def clear(self) -> None:
        payload = {"type": "clear"}
        self._last_payload = None
        await self._broadcast(payload)

    async def register_audio(self, token: str, audio_path: str) -> bool:
        """登记一段短期 TTS 音频，并广播给 overlay 客户端。"""
        token = (token or "").strip()
        path = str(audio_path or "").strip()
        if not token or not path:
            return False
        try:
            normalized = os.path.abspath(path)
        except (TypeError, ValueError):
            return False
        if not os.path.isfile(normalized):
            return False
        self._audio_files[token] = normalized
        self._audio_files.move_to_end(token)
        while len(self._audio_files) > MAX_AUDIO_ENTRIES:
            self._audio_files.popitem(last=False)
        await self._broadcast({"type": "audio", "token": token})
        return True

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._clients):
            try:
                await ws.send_str(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "clients": len(self._clients)})

    async def _handle_show(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            try:
                data = await request.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            text = str(data.get("text") or "")
        else:
            text = str(request.query.get("text") or "")
        await self.show(text)
        return web.json_response({"ok": True, "text": text, "clients": len(self._clients)})

    async def _handle_clear(self, request: web.Request) -> web.Response:
        await self.clear()
        return web.json_response({"ok": True, "clients": len(self._clients)})

    async def _handle_audio(self, request: web.Request) -> web.StreamResponse:
        token = str(request.match_info.get("token") or "").strip()
        path = self._audio_files.get(token) if token else ""
        if not path:
            raise web.HTTPNotFound()
        try:
            audio_file = Path(path).resolve(strict=True)
        except (OSError, ValueError):
            self._audio_files.pop(token, None)
            raise web.HTTPNotFound()
        if not audio_file.is_file():
            self._audio_files.pop(token, None)
            raise web.HTTPNotFound()
        return web.FileResponse(
            audio_file,
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = self._render_html()
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        if self._last_payload:
            await ws.send_str(json.dumps(self._last_payload, ensure_ascii=False))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws

    def _render_html(self) -> str:
        style_json = json.dumps(self.style, ensure_ascii=False)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Live2D Subtitle Overlay</title>
  <style>
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: transparent;
      font-family: "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif;
    }}
    #stage {{
      position: fixed;
      inset: 0;
      display: flex;
      align-items: var(--align-y);
      justify-content: center;
      padding: var(--padding);
      box-sizing: border-box;
      pointer-events: none;
    }}
    #subtitle {{
      max-width: min(var(--max-width), 96vw);
      color: var(--text-color);
      font-size: var(--font-size);
      font-weight: var(--font-weight);
      line-height: 1.45;
      text-align: center;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      letter-spacing: 0;
      opacity: 0;
      transform: translateY(8px);
      transition: opacity 180ms ease, transform 180ms ease;
      text-shadow:
        0 0 var(--stroke-size) var(--stroke-color),
        0 0 var(--stroke-size) var(--stroke-color),
        0 2px 8px rgba(0,0,0,.42);
    }}
    #subtitle.visible {{
      opacity: 1;
      transform: translateY(0);
    }}
    #subtitle.fade {{
      opacity: 0;
      transform: translateY(-4px);
    }}
    #cursor {{
      display: inline-block;
      width: .55em;
      margin-left: .08em;
      color: var(--cursor-color);
      animation: blink 900ms steps(2, start) infinite;
    }}
    @keyframes blink {{
      0%, 45% {{ opacity: 1; }}
      46%, 100% {{ opacity: 0; }}
    }}
  </style>
</head>
<body>
  <div id="stage"><div id="subtitle"></div></div>
  <audio id="tts-audio" hidden preload="auto"></audio>
  <script>
    const defaultStyle = {style_json};
    const root = document.documentElement;
    const subtitle = document.getElementById("subtitle");
    const ttsAudio = document.getElementById("tts-audio");
    let typingToken = 0;

    function sleep(ms) {{
      return new Promise(resolve => setTimeout(resolve, ms));
    }}

    function applyStyle(style) {{
      const s = Object.assign({{}}, defaultStyle, style || {{}});
      root.style.setProperty("--align-y", s.position === "top" ? "flex-start" : (s.position === "center" ? "center" : "flex-end"));
      root.style.setProperty("--padding", `${{s.padding || 48}}px`);
      root.style.setProperty("--max-width", `${{s.max_width || 1100}}px`);
      root.style.setProperty("--text-color", s.text_color || "#ffffff");
      root.style.setProperty("--stroke-color", s.stroke_color || "#111111");
      root.style.setProperty("--stroke-size", `${{s.stroke_size || 4}}px`);
      root.style.setProperty("--font-size", `${{s.font_size || 42}}px`);
      root.style.setProperty("--font-weight", String(s.font_weight || 700));
      root.style.setProperty("--cursor-color", s.cursor_color || s.text_color || "#ffffff");
      return s;
    }}

    async function typeSubtitle(text, style) {{
      const s = applyStyle(style);
      const token = ++typingToken;
      subtitle.className = "";
      subtitle.textContent = "";
      await sleep(20);
      if (token !== typingToken) return;
      subtitle.className = "visible";

      const cursor = s.show_cursor === false ? "" : "▋";
      let current = "";
      const chars = Array.from(text || "");
      const speed = Math.max(1, Number(s.typing_speed_ms || 45));
      for (const char of chars) {{
        if (token !== typingToken) return;
        current += char;
        subtitle.textContent = current + cursor;
        await sleep(speed);
      }}
      if (token !== typingToken) return;
      subtitle.textContent = current;

      await sleep(Math.max(0, Number(s.hold_seconds || 4)) * 1000);
      if (token !== typingToken) return;
      if (s.fade_out !== false) {{
        subtitle.className = "visible fade";
        await sleep(400);
      }}
      if (token === typingToken) {{
        subtitle.className = "";
        subtitle.textContent = "";
      }}
    }}

    function clearSubtitle() {{
      typingToken++;
      subtitle.className = "";
      subtitle.textContent = "";
    }}

    function releaseAudio() {{
      ttsAudio.pause();
      ttsAudio.removeAttribute("src");
      ttsAudio.load();
    }}

    function playAudio(token) {{
      if (!token) return;
      releaseAudio();
      ttsAudio.src = `${{location.origin}}/audio/${{encodeURIComponent(token)}}`;
      const attempt = ttsAudio.play();
      if (attempt && typeof attempt.catch === "function") {{
        attempt.catch(() => {{}});
      }}
    }}

    ttsAudio.addEventListener("ended", releaseAudio);
    ttsAudio.addEventListener("error", releaseAudio);

    function connect() {{
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${{protocol}}//${{location.host}}/ws`);
      ws.onmessage = event => {{
        const payload = JSON.parse(event.data);
        if (payload.type === "subtitle") typeSubtitle(payload.text, payload.style);
        if (payload.type === "clear") {{ clearSubtitle(); releaseAudio(); }}
        if (payload.type === "audio") playAudio(payload.token);
      }};
      ws.onclose = () => setTimeout(connect, 1000);
    }}

    applyStyle(defaultStyle);
    connect();
  </script>
</body>
</html>"""
