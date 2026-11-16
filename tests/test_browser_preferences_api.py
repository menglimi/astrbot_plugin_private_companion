"""Role and card preferences share one browser cookie, never plugin storage."""

import base64
from email.message import Message
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from urllib.request import Request as URLRequest

import tests.astrbot_data_root  # noqa: F401
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.browser_preferences import (
    BROWSER_PREFERENCE_MAX_AGE,
    MAX_BROWSER_PREFERENCE_COOKIE_BYTES,
    browser_preference_cookie_name,
    encode_browser_preferences,
)
from astrbot_host.page_api import PageWebAPI, PREFIX


PLUGIN_NAME = "astrbot_plugin_private_companion"
API_PATH = "/api/v1/plugins/extensions" + PREFIX + "/page/browser-preferences"
SELECTION = {"bot_id": "synthetic-bot", "role_id": "合成角色", "target_id": None}
POSITION = {"right": 325.5, "bottom": 40}
EMPTY_PREFERENCES = {"selection": None, "room_card_position": None}


def payload(response):
    return response if isinstance(response, dict) else json.loads(response.body)


def cookies_from(response):
    cookies = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        cookies.load(header)
    return {name: morsel.value for name, morsel in cookies.items() if morsel["max-age"] != "0"}


class BrowserPreferencesApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="companion-browser-preferences-")
        self.controller = NS(data_root=Path(self.directory.name), _terminated=False,
                             runtime=NS(installation_id="synthetic-installation"))
        self.api = PageWebAPI(self.controller)

    def tearDown(self):
        self.assertEqual(list(Path(self.directory.name).iterdir()), [],
                         "browser preferences must not create server-side data")
        self.directory.cleanup()

    async def call(self, handler, body=None, *, cookies=None, username="administrator",
                   plugin_name=PLUGIN_NAME, query="", raw=None):
        body_bytes = raw if raw is not None else json.dumps(body or {}).encode("utf-8")

        async def receive():
            return {"type": "http.request", "body": body_bytes}

        cookie_header = "; ".join(f"{name}={value}" for name, value in (cookies or {}).items())
        raw_request = Request({"type": "http", "scheme": "http", "server": ("testserver", 80),
                               "path": API_PATH, "method": "GET" if body is None else "POST",
                               "query_string": query.encode("ascii"),
                               "headers": [(b"cookie", cookie_header.encode("ascii"))]}, receive)
        request = PluginRequest(raw_request, username=username, plugin_name=plugin_name)
        with bind_request_context(request):
            return await handler()

    async def test_routes_use_the_dashboard_page_api(self):
        routes = {(path, tuple(methods)): handler for path, handler, methods, _ in self.api.routes()}
        endpoint = PREFIX + "/page/browser-preferences"
        self.assertEqual(routes[(endpoint, ("GET",))], self.api.browser_preferences)
        self.assertEqual(routes[(endpoint, ("POST",))], self.api.save_browser_preferences)

    async def test_absent_cookies_return_explicit_empty_preferences(self):
        response = await self.call(self.api.browser_preferences)
        self.assertEqual(payload(response)["data"]["preferences"], EMPTY_PREFERENCES)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)

    async def test_cookie_round_trip_survives_api_restart_without_server_storage(self):
        preferences = {"selection": SELECTION, "room_card_position": POSITION}
        saved = await self.call(self.api.save_browser_preferences, preferences)
        self.assertEqual(payload(saved)["data"]["preferences"], preferences)
        self.assertEqual(len(saved.headers.getlist("set-cookie")), 1)
        encoded = next(iter(cookies_from(saved).values()))
        self.assertEqual(json.loads(base64.urlsafe_b64decode(encoded)),
                         {"version": 1, "preferences": preferences})
        restarted = PageWebAPI(self.controller)
        restored = await self.call(restarted.browser_preferences, cookies=cookies_from(saved))
        self.assertEqual(payload(restored)["data"]["preferences"], preferences)

    async def test_selection_preserves_an_explicit_window(self):
        selection = {**SELECTION, "target_id": "synthetic-group-window"}
        saved = await self.call(self.api.save_browser_preferences, {"selection": selection})
        restored = await self.call(self.api.browser_preferences, cookies=cookies_from(saved))
        self.assertEqual(payload(restored)["data"]["preferences"]["selection"], selection)

    async def test_legacy_position_cookie_preserves_role_selection(self):
        preferences = {"selection": SELECTION, "room_card_position": {"left": 325.5, "top": 40}}
        original = await self.call(self.api.save_browser_preferences, preferences)
        cookies = cookies_from(original)
        restored = await self.call(self.api.browser_preferences, cookies=cookies)
        self.assertEqual(payload(restored)["data"]["preferences"], preferences)

    async def test_left_bottom_anchor_round_trip(self):
        position = {"left": 80.5, "bottom": 60}
        saved = await self.call(self.api.save_browser_preferences,
                                {"selection": SELECTION, "room_card_position": position})
        restored = await self.call(self.api.browser_preferences, cookies=cookies_from(saved))
        self.assertEqual(payload(restored)["data"]["preferences"],
                         {"selection": SELECTION, "room_card_position": position})

    async def test_cookie_is_persistent_and_only_sent_to_the_preferences_endpoint(self):
        saved = await self.call(self.api.save_browser_preferences, {"selection": SELECTION})
        header = saved.headers["set-cookie"]
        cookie = next(iter(SimpleCookie(header).values()))
        self.assertEqual(cookie["path"], API_PATH)
        self.assertEqual(cookie["max-age"], str(BROWSER_PREFERENCE_MAX_AGE))
        self.assertTrue(cookie["expires"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "lax")

        headers = Message()
        headers.add_header("Set-Cookie", header)
        jar = CookieJar()
        url = "http://testserver" + API_PATH
        jar.extract_cookies(NS(info=lambda: headers), URLRequest(url))
        request = URLRequest(url)
        jar.add_cookie_header(request)
        self.assertTrue(request.get_header("Cookie"))
        unrelated = URLRequest("http://testserver/api/v1/plugins/extensions/another_plugin/records")
        jar.add_cookie_header(unrelated)
        self.assertIsNone(unrelated.get_header("Cookie"))
        expires = next(iter(jar)).expires
        with patch("http.cookiejar.time.time", return_value=expires + 1):
            expired = URLRequest(url)
            jar.add_cookie_header(expired)
        self.assertIsNone(expired.get_header("Cookie"))

    async def test_accounts_and_installations_have_independent_cookies(self):
        saved = await self.call(self.api.save_browser_preferences, {"selection": SELECTION})
        cookies = cookies_from(saved)
        other_user = await self.call(self.api.browser_preferences, cookies=cookies, username="other-admin")
        self.assertEqual(payload(other_user)["data"]["preferences"], EMPTY_PREFERENCES)
        self.controller.runtime.installation_id = "another-installation"
        other_installation = await self.call(self.api.browser_preferences, cookies=cookies)
        self.assertEqual(payload(other_installation)["data"]["preferences"], EMPTY_PREFERENCES)

    async def test_partial_updates_merge_with_the_existing_cookie(self):
        selection = await self.call(self.api.save_browser_preferences, {"selection": SELECTION})
        position = await self.call(self.api.save_browser_preferences, {"room_card_position": POSITION},
                                   cookies=cookies_from(selection))
        selection_cookies, position_cookies = cookies_from(selection), cookies_from(position)
        self.assertEqual(len(selection_cookies), 1)
        self.assertEqual(len(position_cookies), 1)
        self.assertEqual(set(selection_cookies), set(position_cookies))
        restored = await self.call(self.api.browser_preferences, cookies=position_cookies)
        self.assertEqual(payload(restored)["data"]["preferences"],
                         {"selection": SELECTION, "room_card_position": POSITION})

    async def test_clearing_one_preference_keeps_the_other(self):
        saved = await self.call(self.api.save_browser_preferences,
                                {"selection": SELECTION, "room_card_position": POSITION})
        cookies = cookies_from(saved)
        cleared = await self.call(self.api.save_browser_preferences, {"selection": None}, cookies=cookies)
        self.assertEqual(payload(cleared)["data"]["preferences"],
                         {"selection": None, "room_card_position": POSITION})
        self.assertEqual(len(cleared.headers.getlist("set-cookie")), 1)
        stored = next(iter(SimpleCookie(cleared.headers["set-cookie"]).values()))
        self.assertEqual(stored["max-age"], str(BROWSER_PREFERENCE_MAX_AGE))
        restored = await self.call(self.api.browser_preferences, cookies=cookies_from(cleared))
        self.assertEqual(payload(restored)["data"]["preferences"],
                         {"selection": None, "room_card_position": POSITION})

    async def test_clearing_all_preferences_deletes_the_cookie(self):
        saved = await self.call(self.api.save_browser_preferences,
                                {"selection": SELECTION, "room_card_position": POSITION})
        cleared = await self.call(self.api.save_browser_preferences, EMPTY_PREFERENCES,
                                  cookies=cookies_from(saved))
        self.assertEqual(payload(cleared)["data"]["preferences"], EMPTY_PREFERENCES)
        self.assertEqual(len(cleared.headers.getlist("set-cookie")), 1)
        deleted = next(iter(SimpleCookie(cleared.headers["set-cookie"]).values()))
        self.assertEqual(deleted["max-age"], "0")
        self.assertEqual(deleted["path"], API_PATH)
        self.assertEqual(cookies_from(cleared), {})

    async def test_invalid_cookie_returns_empty_preferences(self):
        name = browser_preference_cookie_name(self.controller.runtime.installation_id, "administrator")
        invalid_values = ("not-base64!", "A" * (MAX_BROWSER_PREFERENCE_COOKIE_BYTES + 1),
                          encode_browser_preferences({"selection": {"role_id": "missing-bot"},
                                                      "room_card_position": POSITION}),
                          encode_browser_preferences({"selection": SELECTION}),
                          base64.urlsafe_b64encode(b'{"version":2,"preferences":{}}').decode("ascii"),
                          base64.urlsafe_b64encode(b"not-json").decode("ascii"))
        for invalid in invalid_values:
            with self.subTest(invalid=invalid[:40]):
                response = await self.call(self.api.browser_preferences, cookies={name: invalid})
                self.assertEqual(payload(response)["data"]["preferences"], EMPTY_PREFERENCES)

    async def test_invalid_inputs_are_rejected_without_setting_cookies(self):
        invalid_values = ({}, [], {"unknown": True}, {"selection": {"bot_id": "test"}},
                          {"selection": {**SELECTION, "role_id": ""}},
                          {"selection": {**SELECTION, "target_id": 1}},
                          {"selection": {**SELECTION, "extra": True}},
                          {"selection": {**SELECTION, "role_id": "x" * 513}},
                          {"room_card_position": {"left": True, "top": 0}},
                          {"room_card_position": {"left": -1, "top": 0}},
                          {"room_card_position": {"left": float("nan"), "top": 0}},
                          {"room_card_position": {"left": float("inf"), "top": 0}},
                          {"room_card_position": {"right": -1, "bottom": 0}},
                          {"room_card_position": {"right": True, "bottom": 0}},
                          {"room_card_position": {"right": 0, "bottom": float("nan")}},
                          {"room_card_position": {"right": 0, "top": 0}})
        for value in invalid_values:
            with self.subTest(value=value):
                rejected = await self.call(self.api.save_browser_preferences, value)
                self.assertEqual(payload(rejected)["data"]["code"], "browser_preferences_invalid")

    async def test_rejects_malformed_and_oversized_requests(self):
        for raw in (b"{", b"\xff", b" " * 8193,
                    json.dumps({"selection": {"bot_id": "角" * 500, "role_id": "色" * 500,
                                              "target_id": None}}, ensure_ascii=False).encode("utf-8")):
            rejected = await self.call(self.api.save_browser_preferences, raw=raw)
            self.assertEqual(payload(rejected)["data"]["code"], "browser_preferences_invalid")

    async def test_size_limit_applies_to_the_merged_cookie(self):
        selection = {"bot_id": "甲" * 512, "role_id": "乙" * 320, "target_id": None}
        saved = await self.call(self.api.save_browser_preferences, {"selection": selection})
        self.assertEqual(payload(saved)["status"], "ok")
        cookies = cookies_from(saved)
        rejected = await self.call(self.api.save_browser_preferences, {"room_card_position": POSITION},
                                   cookies=cookies)
        self.assertEqual(payload(rejected)["data"]["code"], "browser_preferences_invalid")
        restored = await self.call(self.api.browser_preferences, cookies=cookies)
        self.assertEqual(payload(restored)["data"]["preferences"],
                         {"selection": selection, "room_card_position": None})

    async def test_requires_dashboard_identity_and_active_plugin(self):
        for handler in (self.api.browser_preferences, self.api.save_browser_preferences):
            for overrides in ({"username": "api_key:synthetic"}, {"username": ""},
                              {"plugin_name": "another_plugin"}, {"query": "username=other"}):
                rejected = await self.call(handler, {"selection": SELECTION}, **overrides)
                self.assertEqual(payload(rejected)["status"], "error")
        self.controller.runtime = None
        rejected = await self.call(self.api.browser_preferences)
        self.assertEqual(payload(rejected)["data"]["code"], "staging_not_initialized")
        self.controller._terminated = True
        rejected = await self.call(self.api.browser_preferences)
        self.assertEqual(payload(rejected)["data"]["code"], "staging_host_terminated")


if __name__ == "__main__":
    unittest.main()
