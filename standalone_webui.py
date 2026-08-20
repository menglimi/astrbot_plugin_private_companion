# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover - only used by isolated import checks
    logger = logging.getLogger(__name__)

try:
    from quart import Quart as _Quart
    from quart import Response as _QuartResponse
    from quart import g as _quart_g
    from quart import jsonify as _jsonify
    from quart import request as _request
    from quart import send_file as _send_file

    _QUART_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised when AstrBot deps are absent
    _Quart = None
    _QuartResponse = None
    _quart_g = None
    _jsonify = None
    _request = None
    _send_file = None
    _QUART_IMPORT_ERROR = type(exc).__name__

try:
    from hypercorn.asyncio import serve as _hypercorn_serve
    from hypercorn.config import Config as _HypercornConfig

    _HYPERCORN_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised when AstrBot deps are absent
    _hypercorn_serve = None
    _HypercornConfig = None
    _HYPERCORN_IMPORT_ERROR = type(exc).__name__


API_PREFIX = "/api/v1"
BRIDGE_API_PREFIX = "/astrbot_plugin_private_companion/page"
SESSION_COOKIE_NAME = "private_companion_session"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PUBLIC_AUTH_PATHS = frozenset(
    {
        f"{API_PREFIX}/auth/login",
        f"{API_PREFIX}/auth/status",
        f"{API_PREFIX}/auth/session",
    }
)

_LOGIN_BODY_LIMIT = 4096
_MAX_TOKEN_LENGTH = 4096
_MAX_SESSIONS = 512
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_WINDOW_SECONDS = 300.0
_MAX_API_BODY_BYTES = 24 * 1024 * 1024

_STANDALONE_META = (
    f'<meta name="private-companion-mode" content="standalone" />\n'
    f'  <meta name="private-companion-api-base" content="{API_PREFIX}" />'
)
_STANDALONE_SCRIPT = '<script src="./standalone.js?v=1"></script>'

_LOGIN_PAGE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex,nofollow" />
  <title>陪伴面板登录</title>
  <style>
    :root { color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f3f5f7; color: #172033; }
    main { width: min(100%, 400px); border: 1px solid #d7dde5; border-radius: 6px; padding: 28px; background: #fff; box-shadow: 0 12px 30px rgba(23, 32, 51, .08); }
    h1 { margin: 0; font-size: 24px; line-height: 1.3; letter-spacing: 0; }
    p { margin: 8px 0 24px; color: #5c6675; font-size: 14px; line-height: 1.6; }
    label { display: block; margin-bottom: 8px; font-size: 14px; font-weight: 600; }
    input { width: 100%; height: 44px; border: 1px solid #b9c2cf; border-radius: 4px; padding: 0 12px; background: #fff; color: #172033; font: inherit; outline: none; }
    input:focus { border-color: #1769aa; box-shadow: 0 0 0 3px rgba(23, 105, 170, .14); }
    button { width: 100%; min-height: 44px; margin-top: 16px; border: 0; border-radius: 4px; padding: 10px 16px; background: #1769aa; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
    button:hover { background: #115889; }
    button:disabled { cursor: wait; opacity: .68; }
    #error { min-height: 22px; margin: 12px 0 0; color: #a2352a; font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <h1>陪伴面板</h1>
    <p>使用插件配置中的访问令牌登录。</p>
    <form id="login-form">
      <label for="token">访问令牌</label>
      <input id="token" name="token" type="password" autocomplete="current-password" required autofocus />
      <button id="submit" type="submit">登录</button>
      <div id="error" role="alert" aria-live="polite"></div>
    </form>
  </main>
  <script>
    const form = document.getElementById("login-form");
    const tokenInput = document.getElementById("token");
    const submit = document.getElementById("submit");
    const error = document.getElementById("error");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      submit.disabled = true;
      try {
        const response = await fetch("/api/v1/auth/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: tokenInput.value })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.authenticated === false || payload.success === false) {
          throw new Error(response.status === 429 ? "尝试次数过多，请稍后再试。" : "访问令牌不正确。");
        }
        tokenInput.value = "";
        window.location.replace("/");
      } catch (reason) {
        error.textContent = reason instanceof Error ? reason.message : "登录失败，请稍后重试。";
        tokenInput.select();
      } finally {
        submit.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


class _ContextRouteCollector:
    """Temporarily collect legacy register_web_api calls without mutating AstrBot."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.routes: list[tuple[str, Any, list[str], str]] = []

    def register_web_api(
        self,
        path: str,
        handler: Any,
        methods: Iterable[str] | None = None,
        description: str = "",
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        normalized_methods = [str(item).upper() for item in (methods or ("GET",))]
        self.routes.append(
            (str(path), handler, normalized_methods, str(description or ""))
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class StandaloneWebUIServer:
    """Serve the companion panel beside AstrBot's existing Page bridge."""

    def __init__(
        self,
        plugin: Any,
        page_api: Any | None = None,
        *,
        enabled: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        api_token: str | None = None,
        session_ttl_seconds: int | None = None,
        secure_cookie: bool | None = None,
        allowed_origins: Iterable[str] | None = None,
        static_root: str | Path | None = None,
    ) -> None:
        self.plugin = plugin
        self.page_api = page_api
        self.enabled = self._as_bool(
            getattr(plugin, "enable_standalone_webui", False)
            if enabled is None
            else enabled
        )
        self.host = (
            str(
                getattr(plugin, "standalone_webui_host", "127.0.0.1")
                if host is None
                else host
            ).strip()
            or "127.0.0.1"
        )
        self.port = self._bounded_int(
            getattr(plugin, "standalone_webui_port", 6190) if port is None else port,
            default=6190,
            minimum=1,
            maximum=65535,
        )

        configured_token = (
            getattr(plugin, "standalone_webui_access_token", "")
            if api_token is None
            else api_token
        )
        token_text = str(configured_token or "")
        self._has_access_token = bool(token_text)
        self._access_token_length = len(token_text)
        self._access_token_digest = (
            self._digest_token(token_text) if token_text else b""
        )
        token_text = ""

        if session_ttl_seconds is None:
            ttl_hours = self._bounded_int(
                getattr(plugin, "standalone_webui_session_ttl_hours", 24),
                default=24,
                minimum=1,
                maximum=168,
            )
            session_ttl_seconds = ttl_hours * 3600
        self.session_ttl_seconds = self._bounded_int(
            session_ttl_seconds,
            default=24 * 3600,
            minimum=1,
            maximum=7 * 24 * 3600,
        )
        self.secure_cookie = secure_cookie
        self.static_root = Path(
            static_root or Path(__file__).parent / "pages" / "companion-panel"
        ).resolve()
        self.allowed_origins = {
            normalized
            for item in (allowed_origins or ())
            if (normalized := self._normalize_origin(str(item or "")))
        }

        self._sessions: dict[bytes, float] = {}
        self._login_failures: dict[str, deque[float]] = {}
        self._auth_lock = asyncio.Lock()
        self._app: Any | None = None
        self._panel_html: str | None = None
        self._serve_task: asyncio.Task[Any] | None = None
        self._shutdown_event: asyncio.Event | None = None

    @property
    def app(self) -> Any | None:
        return self._app

    @property
    def is_running(self) -> bool:
        task = self._serve_task
        return isinstance(task, asyncio.Task) and not task.done()

    @property
    def url(self) -> str:
        display_host = (
            "127.0.0.1" if self.host in {"0.0.0.0", "::", "[::]"} else self.host
        )
        if ":" in display_host and not display_host.startswith("["):
            display_host = f"[{display_host}]"
        return f"http://{display_host}:{self.port}/"

    @classmethod
    def dependencies_available(cls) -> bool:
        del cls
        return all(
            item is not None
            for item in (
                _Quart,
                _QuartResponse,
                _request,
                _jsonify,
                _send_file,
                _hypercorn_serve,
                _HypercornConfig,
            )
        )

    def create_app(self) -> Any:
        if (
            _Quart is None
            or _QuartResponse is None
            or _request is None
            or _jsonify is None
            or _send_file is None
        ):
            raise RuntimeError(
                f"Quart unavailable ({_QUART_IMPORT_ERROR or 'unknown import error'})"
            )
        if self._app is not None:
            return self._app

        app = _Quart("private_companion_standalone_webui", static_folder=None)
        app.config["MAX_CONTENT_LENGTH"] = _MAX_API_BODY_BYTES
        app.config["PROVIDE_AUTOMATIC_OPTIONS"] = True
        self._register_transport_hooks(app)
        self._register_auth_routes(app)
        self._register_static_routes(app)
        self._register_page_api_routes(app)
        self._app = app
        return app

    async def start(self) -> bool:
        if self.is_running:
            return True
        if not self.enabled:
            logger.debug("[PrivateCompanion] 独立 WebUI 未启用")
            return False
        if not self._has_access_token or self._access_token_length < 16:
            logger.warning(
                "[PrivateCompanion] 独立 WebUI 未启动: 访问令牌至少需要 16 个字符"
            )
            return False
        if self.page_api is None:
            logger.warning("[PrivateCompanion] 独立 WebUI 未启动: Page API 不可用")
            return False
        if not self.dependencies_available():
            logger.warning(
                "[PrivateCompanion] 独立 WebUI 未启动: 缺少 Quart/Hypercorn 运行依赖 (quart=%s hypercorn=%s)",
                _QUART_IMPORT_ERROR or "ok",
                _HYPERCORN_IMPORT_ERROR or "ok",
            )
            return False
        if not self._resolve_static_path("index.html"):
            logger.warning("[PrivateCompanion] 独立 WebUI 未启动: 面板静态文件不存在")
            return False

        try:
            app = self.create_app()
            config = _HypercornConfig()
            config.bind = [self._hypercorn_bind()]
            config.use_reloader = False
            config.graceful_timeout = 8.0
            config.shutdown_timeout = 8.0
            config.loglevel = "warning"
            shutdown_event = asyncio.Event()
            self._shutdown_event = shutdown_event
            task = asyncio.create_task(
                _hypercorn_serve(app, config, shutdown_trigger=shutdown_event.wait),
                name="private-companion-standalone-webui",
            )
            self._serve_task = task
            task.add_done_callback(self._consume_serve_result)
            await asyncio.sleep(0)
            if task.done():
                return False
        except Exception as exc:
            self._serve_task = None
            self._shutdown_event = None
            logger.warning(
                "[PrivateCompanion] 独立 WebUI 启动失败: error_type=%s",
                type(exc).__name__,
            )
            return False

        logger.info("[PrivateCompanion] 独立 WebUI 已启动: %s", self.url)
        return True

    async def stop(self) -> None:
        task = self._serve_task
        shutdown_event = self._shutdown_event
        if shutdown_event is not None:
            shutdown_event.set()
        if isinstance(task, asyncio.Task) and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[PrivateCompanion] 独立 WebUI 优雅停止超时,正在取消服务任务"
                )
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        elif isinstance(task, asyncio.Task):
            await asyncio.gather(task, return_exceptions=True)
        self._serve_task = None
        self._shutdown_event = None
        async with self._auth_lock:
            self._sessions.clear()
            self._login_failures.clear()

    def _register_transport_hooks(self, app: Any) -> None:
        @app.before_request
        async def standalone_auth_guard() -> Any | None:
            path = str(_request.path or "")
            if not path.startswith(f"{API_PREFIX}/") or path in PUBLIC_AUTH_PATHS:
                return None
            identity = await self._authenticate_request()
            if identity is None:
                response = self._json_response(False, status=401, error="unauthorized")
                response.headers["WWW-Authenticate"] = (
                    'Bearer realm="private-companion"'
                )
                return response
            _quart_g.private_companion_auth = identity
            if str(_request.method or "").upper() in UNSAFE_METHODS:
                if (
                    identity["source"] == "cookie"
                    and not self._request_origin_allowed()
                ):
                    self._log_auth_rejection("csrf_origin")
                    return self._json_response(
                        False, status=403, error="origin_rejected"
                    )
            return None

        @app.after_request
        async def standalone_security_headers(response: Any) -> Any:
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob: http: https:; "
                "font-src 'self' data: https:; connect-src 'self'; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
            )
            response.headers.setdefault(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            )
            path = str(_request.path or "")
            if path == "/" or path == "/index.html" or path.startswith(API_PREFIX):
                response.headers["Cache-Control"] = "no-store"
            return response

        @app.errorhandler(404)
        async def standalone_not_found(_error: Any) -> Any:
            if str(_request.path or "").startswith(API_PREFIX):
                return self._json_response(False, status=404, error="not_found")
            return _QuartResponse(
                "Not found", status=404, content_type="text/plain; charset=utf-8"
            )

        @app.errorhandler(405)
        async def standalone_method_not_allowed(_error: Any) -> Any:
            if str(_request.path or "").startswith(API_PREFIX):
                return self._json_response(
                    False, status=405, error="method_not_allowed"
                )
            return _QuartResponse(
                "Method not allowed",
                status=405,
                content_type="text/plain; charset=utf-8",
            )

        @app.errorhandler(413)
        async def standalone_request_too_large(_error: Any) -> Any:
            return self._json_response(False, status=413, error="request_too_large")

        @app.errorhandler(500)
        async def standalone_internal_error(_error: Any) -> Any:
            logger.warning(
                "[PrivateCompanion] 独立 WebUI 请求失败: path=%s reason=internal_error",
                str(_request.path or "")[:160],
            )
            if str(_request.path or "").startswith(API_PREFIX):
                return self._json_response(False, status=500, error="internal_error")
            return _QuartResponse(
                "Internal server error",
                status=500,
                content_type="text/plain; charset=utf-8",
            )

    def _register_auth_routes(self, app: Any) -> None:
        app.add_url_rule(
            f"{API_PREFIX}/auth/login",
            endpoint="standalone_auth_login",
            view_func=self._login,
            methods=["POST"],
        )
        app.add_url_rule(
            f"{API_PREFIX}/auth/status",
            endpoint="standalone_auth_status",
            view_func=self._auth_status,
            methods=["GET"],
        )
        app.add_url_rule(
            f"{API_PREFIX}/auth/session",
            endpoint="standalone_auth_session",
            view_func=self._auth_status,
            methods=["GET"],
        )
        app.add_url_rule(
            f"{API_PREFIX}/auth/logout",
            endpoint="standalone_auth_logout",
            view_func=self._logout,
            methods=["POST"],
        )

    def _register_static_routes(self, app: Any) -> None:
        app.add_url_rule(
            "/", endpoint="standalone_root", view_func=self._root, methods=["GET"]
        )
        app.add_url_rule(
            "/index.html",
            endpoint="standalone_index",
            view_func=self._root,
            methods=["GET"],
        )

        async def app_css() -> Any:
            return await self._static_response("app.css")

        async def app_js() -> Any:
            return await self._static_response("app.js")

        async def standalone_js() -> Any:
            return await self._static_response("standalone.js")

        async def css_asset(filename: str) -> Any:
            return await self._static_response(f"css/{filename}")

        async def js_asset(filename: str) -> Any:
            return await self._static_response(f"js/{filename}")

        app.add_url_rule(
            "/app.css",
            endpoint="standalone_app_css",
            view_func=app_css,
            methods=["GET"],
        )
        app.add_url_rule(
            "/app.js", endpoint="standalone_app_js", view_func=app_js, methods=["GET"]
        )
        app.add_url_rule(
            "/standalone.js",
            endpoint="standalone_auth_js",
            view_func=standalone_js,
            methods=["GET"],
        )
        app.add_url_rule(
            "/css/<path:filename>",
            endpoint="standalone_css",
            view_func=css_asset,
            methods=["GET"],
        )
        app.add_url_rule(
            "/js/<path:filename>",
            endpoint="standalone_js",
            view_func=js_asset,
            methods=["GET"],
        )

    def _register_page_api_routes(self, app: Any) -> None:
        for index, (path, handler, methods, _description) in enumerate(
            self._page_route_bindings()
        ):
            suffix = self._standalone_route_suffix(path)
            if not suffix:
                continue
            rule = f"{API_PREFIX}{suffix}"
            if rule.startswith(f"{API_PREFIX}/auth/"):
                logger.warning(
                    "[PrivateCompanion] 独立 WebUI 跳过冲突的 Page API 路由: %s", rule
                )
                continue
            app.add_url_rule(
                rule,
                endpoint=f"standalone_page_{index:03d}",
                view_func=handler,
                methods=[str(item).upper() for item in methods],
            )

    def _page_route_bindings(self) -> list[tuple[str, Any, list[str], str]]:
        if self.page_api is None:
            return []
        route_bindings = getattr(self.page_api, "route_bindings", None)
        if callable(route_bindings):
            return [
                (
                    str(path),
                    handler,
                    [str(item).upper() for item in methods],
                    str(description or ""),
                )
                for path, handler, methods, description in route_bindings()
            ]

        register_routes = getattr(self.page_api, "register_routes", None)
        original_context = getattr(self.plugin, "context", None)
        if not callable(register_routes) or original_context is None:
            return []
        collector = _ContextRouteCollector(original_context)
        try:
            setattr(self.plugin, "context", collector)
            register_routes()
        finally:
            setattr(self.plugin, "context", original_context)
        return collector.routes

    async def _root(self) -> Any:
        return _QuartResponse(
            self._load_panel_html(), status=200, content_type="text/html; charset=utf-8"
        )

    async def _static_response(self, relative_path: str) -> Any:
        resolved = self._resolve_static_path(relative_path)
        if resolved is None:
            return _QuartResponse(
                "Not found", status=404, content_type="text/plain; charset=utf-8"
            )
        try:
            return await _send_file(str(resolved))
        except (FileNotFoundError, IsADirectoryError, OSError):
            return _QuartResponse(
                "Not found", status=404, content_type="text/plain; charset=utf-8"
            )

    async def _login(self) -> Any:
        origin = str(_request.headers.get("Origin") or "").strip()
        if origin and not self._request_origin_allowed():
            self._log_auth_rejection("login_origin")
            return self._json_response(False, status=403, error="origin_rejected")
        if (
            _request.content_length is not None
            and _request.content_length > _LOGIN_BODY_LIMIT
        ):
            self._log_auth_rejection("login_body_too_large")
            return self._json_response(False, status=413, error="request_too_large")
        raw_body = await _request.get_data(cache=True)
        if len(raw_body) > _LOGIN_BODY_LIMIT:
            self._log_auth_rejection("login_body_too_large")
            return self._json_response(False, status=413, error="request_too_large")
        payload = await _request.get_json(silent=True)
        token = payload.get("token") if isinstance(payload, dict) else None
        client_key = self._client_key()
        limited, retry_after = await self._login_is_rate_limited(client_key)
        if limited:
            self._log_auth_rejection("rate_limited", client_key)
            response = self._json_response(False, status=429, error="rate_limited")
            response.headers["Retry-After"] = str(max(1, int(retry_after)))
            return response
        if (
            not isinstance(token, str)
            or len(token) > _MAX_TOKEN_LENGTH
            or not self._access_token_matches(token)
        ):
            await self._record_login_failure(client_key)
            self._log_auth_rejection("invalid_token", client_key)
            return self._json_response(False, status=401, error="invalid_token")

        await self._clear_login_failures(client_key)
        old_cookie = str(_request.cookies.get(SESSION_COOKIE_NAME) or "")
        if old_cookie:
            await self._revoke_session_token(old_cookie)
        session_token, expires_at = await self._issue_session()
        data = {"authenticated": True, "expires_at": int(expires_at)}
        response = self._json_response(True, status=200, data=data, **data)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_token,
            max_age=self.session_ttl_seconds,
            httponly=True,
            secure=self._cookie_secure(),
            samesite="Strict",
            path=API_PREFIX,
        )
        return response

    async def _auth_status(self) -> Any:
        identity = await self._authenticate_request()
        authenticated = identity is not None
        expires_at = (
            int(identity["expires_at"])
            if identity and identity.get("expires_at")
            else None
        )
        data = {"authenticated": authenticated, "expires_at": expires_at}
        return self._json_response(True, status=200, data=data, **data)

    async def _logout(self) -> Any:
        identity = getattr(_quart_g, "private_companion_auth", None)
        if isinstance(identity, dict) and isinstance(
            identity.get("session_digest"), bytes
        ):
            await self._revoke_session_digest(identity["session_digest"])
        cookie_token = str(_request.cookies.get(SESSION_COOKIE_NAME) or "")
        if cookie_token:
            await self._revoke_session_token(cookie_token)
        data = {"authenticated": False, "expires_at": None}
        response = self._json_response(True, status=200, data=data, **data)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            "",
            max_age=0,
            expires=0,
            httponly=True,
            secure=self._cookie_secure(),
            samesite="Strict",
            path=API_PREFIX,
        )
        return response

    async def _authenticate_request(self) -> dict[str, Any] | None:
        authorization = str(_request.headers.get("Authorization") or "")
        if authorization:
            parts = authorization.split(None, 1)
            if len(parts) != 2 or parts[0].casefold() != "bearer":
                return None
            candidate = parts[1]
            if not candidate or len(candidate) > _MAX_TOKEN_LENGTH:
                return None
            if self._access_token_matches(candidate):
                return {
                    "source": "bearer",
                    "kind": "access_token",
                    "expires_at": None,
                    "session_digest": None,
                }
            session = await self._lookup_session(candidate)
            if session is not None:
                digest, expires_at = session
                return {
                    "source": "bearer",
                    "kind": "session",
                    "expires_at": expires_at,
                    "session_digest": digest,
                }
            return None

        candidate = str(_request.cookies.get(SESSION_COOKIE_NAME) or "")
        session = await self._lookup_session(candidate)
        if session is None:
            return None
        digest, expires_at = session
        return {
            "source": "cookie",
            "kind": "session",
            "expires_at": expires_at,
            "session_digest": digest,
        }

    async def _issue_session(self) -> tuple[str, float]:
        session_token = secrets.token_urlsafe(32)
        digest = self._digest_token(session_token)
        expires_at = time.time() + self.session_ttl_seconds
        async with self._auth_lock:
            self._prune_sessions_locked(time.time())
            while len(self._sessions) >= _MAX_SESSIONS:
                oldest = min(self._sessions, key=self._sessions.__getitem__)
                self._sessions.pop(oldest, None)
            self._sessions[digest] = expires_at
        return session_token, expires_at

    async def _lookup_session(self, candidate: str) -> tuple[bytes, float] | None:
        if not candidate or len(candidate) > _MAX_TOKEN_LENGTH:
            return None
        digest = self._digest_token(candidate)
        now = time.time()
        async with self._auth_lock:
            self._prune_sessions_locked(now)
            expires_at = self._sessions.get(digest)
            if expires_at is None or expires_at <= now:
                self._sessions.pop(digest, None)
                return None
            return digest, expires_at

    async def _revoke_session_token(self, candidate: str) -> None:
        if not candidate or len(candidate) > _MAX_TOKEN_LENGTH:
            return
        await self._revoke_session_digest(self._digest_token(candidate))

    async def _revoke_session_digest(self, digest: bytes) -> None:
        async with self._auth_lock:
            self._sessions.pop(digest, None)

    def _prune_sessions_locked(self, now: float) -> None:
        expired = [
            digest for digest, expires_at in self._sessions.items() if expires_at <= now
        ]
        for digest in expired:
            self._sessions.pop(digest, None)

    async def _login_is_rate_limited(self, client_key: str) -> tuple[bool, float]:
        now = time.monotonic()
        async with self._auth_lock:
            failures = self._login_failures.get(client_key)
            if failures is None:
                return False, 0.0
            self._prune_login_failures_locked(failures, now)
            if not failures:
                self._login_failures.pop(client_key, None)
                return False, 0.0
            if len(failures) < _LOGIN_FAILURE_LIMIT:
                return False, 0.0
            retry_after = _LOGIN_FAILURE_WINDOW_SECONDS - (now - failures[0])
            return True, max(1.0, retry_after)

    async def _record_login_failure(self, client_key: str) -> None:
        now = time.monotonic()
        async with self._auth_lock:
            failures = self._login_failures.setdefault(client_key, deque())
            self._prune_login_failures_locked(failures, now)
            failures.append(now)

    async def _clear_login_failures(self, client_key: str) -> None:
        async with self._auth_lock:
            self._login_failures.pop(client_key, None)

    @staticmethod
    def _prune_login_failures_locked(failures: deque[float], now: float) -> None:
        threshold = now - _LOGIN_FAILURE_WINDOW_SECONDS
        while failures and failures[0] <= threshold:
            failures.popleft()

    def _request_origin_allowed(self) -> bool:
        origin = self._normalize_origin(str(_request.headers.get("Origin") or ""))
        if not origin:
            return False
        request_origin = self._normalize_origin(f"{_request.scheme}://{_request.host}")
        return origin == request_origin or origin in self.allowed_origins

    def _cookie_secure(self) -> bool:
        return (
            bool(self.secure_cookie)
            or str(getattr(_request, "scheme", "")).casefold() == "https"
        )

    def _load_panel_html(self) -> str:
        if self._panel_html is not None:
            return self._panel_html
        index_path = self._resolve_static_path("index.html")
        if index_path is None:
            raise FileNotFoundError("companion panel index is missing")
        source = index_path.read_text(encoding="utf-8")
        if 'name="private-companion-mode"' not in source:
            marker = "</head>"
            if marker not in source:
                raise ValueError("companion panel index has no closing head tag")
            source = source.replace(marker, f"  {_STANDALONE_META}\n{marker}", 1)
        if "./standalone.js" not in source:
            app_script = '<script src="./app.js'
            script_index = source.find(app_script)
            if script_index < 0:
                raise ValueError("companion panel index has no app.js script tag")
            line_start = source.rfind("\n", 0, script_index) + 1
            source = (
                source[:line_start] + f"  {_STANDALONE_SCRIPT}\n" + source[line_start:]
            )
        self._panel_html = source
        return source

    def _resolve_static_path(self, relative_path: str) -> Path | None:
        text = str(relative_path or "").replace("\\", "/").lstrip("/")
        if not text or "\x00" in text:
            return None
        try:
            candidate = (self.static_root / text).resolve()
            candidate.relative_to(self.static_root)
        except (OSError, RuntimeError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    def _access_token_matches(self, candidate: str) -> bool:
        if not self._has_access_token or not isinstance(candidate, str):
            return False
        return hmac.compare_digest(
            self._digest_token(candidate), self._access_token_digest
        )

    def _json_response(
        self,
        success: bool,
        *,
        status: int,
        data: Any = None,
        error: str | None = None,
        **fields: Any,
    ) -> Any:
        payload: dict[str, Any] = {"success": bool(success)}
        if data is not None:
            payload["data"] = data
        if error:
            payload["error"] = error
        payload.update(fields)
        response = _jsonify(payload)
        response.status_code = status
        return response

    def _client_key(self) -> str:
        raw = str(getattr(_request, "remote_addr", "") or "unknown")[:96]
        safe = re.sub(r"[^0-9A-Za-z:.%_-]", "_", raw)
        return safe or "unknown"

    def _log_auth_rejection(self, reason: str, client_key: str | None = None) -> None:
        logger.warning(
            "[PrivateCompanion] 独立 WebUI 鉴权拒绝: ip=%s reason=%s",
            client_key or self._client_key(),
            re.sub(r"[^a-z0-9_-]", "_", str(reason or "unknown").casefold())[:48],
        )

    def _hypercorn_bind(self) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self.port}"

    def _consume_serve_result(self, task: asyncio.Task[Any]) -> None:
        if self._serve_task is task:
            self._serve_task = None
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning(
                "[PrivateCompanion] 独立 WebUI 服务任务已结束: error_type=%s",
                type(error).__name__,
            )

    @staticmethod
    def _standalone_route_suffix(path: str) -> str:
        normalized = "/" + str(path or "").lstrip("/")
        if normalized.startswith(BRIDGE_API_PREFIX):
            normalized = normalized[len(BRIDGE_API_PREFIX) :]
        if not normalized.startswith("/") or normalized == "/":
            return ""
        return normalized

    @staticmethod
    def _normalize_origin(value: str) -> str:
        try:
            parsed = urlsplit(str(value or "").strip())
            scheme = parsed.scheme.casefold()
            hostname = (parsed.hostname or "").casefold()
            if (
                scheme not in {"http", "https"}
                or not hostname
                or parsed.username
                or parsed.password
            ):
                return ""
            port = parsed.port
        except (TypeError, ValueError):
            return ""
        default_port = 80 if scheme == "http" else 443
        host = f"[{hostname}]" if ":" in hostname else hostname
        return (
            f"{scheme}://{host}"
            if port in {None, default_port}
            else f"{scheme}://{host}:{port}"
        )

    @staticmethod
    def _digest_token(value: str) -> bytes:
        return hashlib.sha256(str(value).encode("utf-8", errors="strict")).digest()

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
        return bool(value)

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError, OverflowError):
            normalized = default
        return max(minimum, min(maximum, normalized))


# Keep the service-oriented name available for callers and focused tests.
StandaloneWebUIService = StandaloneWebUIServer


__all__ = [
    "API_PREFIX",
    "SESSION_COOKIE_NAME",
    "StandaloneWebUIService",
    "StandaloneWebUIServer",
]
