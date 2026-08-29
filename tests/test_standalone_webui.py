# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "standalone_webui.py"
SPEC = importlib.util.spec_from_file_location(
    "private_companion_standalone_webui_test", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
webui = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = webui
SPEC.loader.exec_module(webui)

ACCESS_TOKEN = "standalo" + "ne-test-token-0123456789"


class _PageApiStub:
    @staticmethod
    async def _probe():
        return {"success": True, "data": {"route": "probe"}}

    @staticmethod
    async def _mutate():
        return {"success": True, "data": {"route": "mutate"}}

    def route_bindings(self):
        return [
            ("/probe", self._probe, ["GET"], "probe"),
            ("/mutate", self._mutate, ["POST"], "mutate"),
        ]


def _server(*, token: str = ACCESS_TOKEN, static_root: Path | None = None):
    plugin = SimpleNamespace(
        enable_standalone_webui=True,
        standalone_webui_host="127.0.0.1",
        standalone_webui_port=6190,
        standalone_webui_access_token=token,
        standalone_webui_session_ttl_hours=24,
    )
    return webui.StandaloneWebUIServer(
        plugin,
        _PageApiStub(),
        static_root=static_root or MODULE_PATH.parent / "pages" / "companion-panel",
    )


class StandaloneImportTests(unittest.TestCase):
    def test_module_import_survives_missing_optional_web_dependencies(self) -> None:
        script = textwrap.dedent(
            f"""
            import builtins
            import importlib.util
            from pathlib import Path

            original_import = builtins.__import__
            def guarded_import(name, *args, **kwargs):
                if name == "quart" or name.startswith("hypercorn"):
                    raise ModuleNotFoundError(name)
                return original_import(name, *args, **kwargs)
            builtins.__import__ = guarded_import
            spec = importlib.util.spec_from_file_location("standalone_without_deps", Path({str(MODULE_PATH)!r}))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            assert module.StandaloneWebUIServer.dependencies_available() is False
            """
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class StandaloneHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_defaults_are_opt_in_and_token_is_masked(self) -> None:
        schema = json.loads(
            (MODULE_PATH.parent / "_conf_schema.json").read_text(encoding="utf-8")
        )
        items = schema["basic_config"]["items"]
        self.assertIs(items["enable_standalone_webui"]["default"], False)
        self.assertEqual(items["standalone_webui_port"]["default"], 6190)
        self.assertEqual(items["standalone_webui_session_ttl_hours"]["default"], 24)
        self.assertIs(items["standalone_webui_access_token"]["password"], True)

    def test_static_path_traversal_is_rejected(self) -> None:
        server = _server()
        self.assertIsNone(server._resolve_static_path("../standalone_webui.py"))
        self.assertIsNone(server._resolve_static_path("css/../../standalone_webui.py"))
        self.assertIsNone(server._resolve_static_path("C:/Windows/win.ini"))
        self.assertIsNotNone(server._resolve_static_path("app.js"))

    def test_index_injects_standalone_metadata_and_script_before_app(self) -> None:
        server = _server()
        html = server._load_panel_html()
        mode_position = html.index('name="private-companion-mode"')
        api_position = html.index('name="private-companion-api-base"')
        standalone_position = html.index('<script src="./standalone.js')
        app_position = html.index('<script src="./app.js')
        self.assertLess(mode_position, html.index("</head>"))
        self.assertLess(api_position, html.index("</head>"))
        self.assertLess(standalone_position, app_position)

        source = (
            MODULE_PATH.parent / "pages" / "companion-panel" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn('name="private-companion-mode"', source)
        self.assertNotIn('<script src="./standalone.js', source)

    async def test_expired_session_is_removed(self) -> None:
        server = _server()
        session_token, _expires_at = await server._issue_session()
        digest = server._digest_token(session_token)
        async with server._auth_lock:
            server._sessions[digest] = time.time() - 1
        self.assertIsNone(await server._lookup_session(session_token))
        self.assertNotIn(digest, server._sessions)

    async def test_missing_access_token_prevents_start(self) -> None:
        server = _server(token="")
        self.assertFalse(await server.start())
        self.assertFalse(server.is_running)
        await server.stop()
        await server.stop()

    @unittest.skipUnless(
        webui.StandaloneWebUIServer.dependencies_available(),
        "Quart/Hypercorn are not installed in this test runtime",
    )
    async def test_start_and_stop_are_idempotent(self) -> None:
        server = _server()

        async def serve_until_shutdown(_app, _config, *, shutdown_trigger):
            await shutdown_trigger()

        with patch.object(webui, "_hypercorn_serve", serve_until_shutdown):
            self.assertTrue(await server.start())
            first_task = server._serve_task
            self.assertTrue(await server.start())
            self.assertIs(server._serve_task, first_task)
            await server.stop()
            await server.stop()
        self.assertFalse(server.is_running)


@unittest.skipUnless(
    webui.StandaloneWebUIServer.dependencies_available(),
    "Quart/Hypercorn are not installed in this test runtime",
)
class StandaloneHttpAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = _server()
        self.app = self.server.create_app()

    async def test_login_cookie_bearer_origin_logout_and_query_rejection(self) -> None:
        client = self.app.test_client()

        wrong = await client.post(
            "/api/v1/auth/login",
            json={"token": "wrong-token"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertFalse((await wrong.get_json())["success"])

        login = await client.post(
            "/api/v1/auth/login",
            json={"token": ACCESS_TOKEN},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(login.status_code, 200)
        payload = await login.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["authenticated"])
        cookie = "\n".join(login.headers.getlist("Set-Cookie"))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/api/v1", cookie)
        self.assertNotIn(ACCESS_TOKEN, cookie)
        parsed_cookie = SimpleCookie()
        parsed_cookie.load(cookie)
        session_token = parsed_cookie[webui.SESSION_COOKIE_NAME].value

        status = await client.get("/api/v1/auth/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue((await status.get_json())["data"]["authenticated"])

        cookie_get = await client.get("/api/v1/probe")
        self.assertEqual(cookie_get.status_code, 200)

        missing_origin = await client.post("/api/v1/mutate")
        self.assertEqual(missing_origin.status_code, 403)
        cross_origin = await client.post(
            "/api/v1/mutate",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(cross_origin.status_code, 403)
        same_origin = await client.post(
            "/api/v1/mutate",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(same_origin.status_code, 200)

        bearer_client = self.app.test_client()
        bearer = await bearer_client.post(
            "/api/v1/mutate",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        )
        self.assertEqual(bearer.status_code, 200)
        session_bearer = await bearer_client.post(
            "/api/v1/mutate",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        self.assertEqual(session_bearer.status_code, 200)

        query_client = self.app.test_client()
        query = await query_client.get(f"/api/v1/probe?token={ACCESS_TOKEN}")
        self.assertEqual(query.status_code, 401)

        logout = await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(logout.status_code, 200)
        cleared_cookie = "\n".join(logout.headers.getlist("Set-Cookie"))
        self.assertIn("Max-Age=0", cleared_cookie)
        after_logout = await client.get("/api/v1/auth/status")
        self.assertFalse((await after_logout.get_json())["data"]["authenticated"])

    async def test_index_static_script_and_security_headers(self) -> None:
        root = await self.app.test_client().get("/")
        self.assertEqual(root.status_code, 200)
        html = (await root.get_data()).decode("utf-8")
        self.assertIn('content="standalone"', html)
        self.assertIn('<script src="./standalone.js', html)
        self.assertLess(html.index("./standalone.js"), html.index("./app.js"))
        self.assertIn("default-src 'self'", root.headers["Content-Security-Policy"])
        self.assertIn(
            "font-src 'self' data: https:", root.headers["Content-Security-Policy"]
        )
        self.assertIn("frame-ancestors 'none'", root.headers["Content-Security-Policy"])
        self.assertIn("camera=()", root.headers["Permissions-Policy"])

        script = await self.app.test_client().get("/standalone.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn(b"/auth/status", await script.get_data())


if __name__ == "__main__":
    unittest.main()
