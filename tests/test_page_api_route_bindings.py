# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quart import Quart

from astrbot_plugin_private_companion.page_api import (
    PAGE_API_PREFIX,
    PrivateCompanionPageApi,
)


class _RegisteringContext:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, list[str], str]] = []

    def register_web_api(
        self,
        route: str,
        handler: object,
        methods: list[str],
        description: str,
    ) -> None:
        self.calls.append((route, handler, methods, description))


class PageApiRouteBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _RegisteringContext()
        self.api = PrivateCompanionPageApi(SimpleNamespace(context=self.context))

    def test_route_bindings_include_overview_and_qzone_method_variants(self) -> None:
        bindings = self.api.route_bindings()
        by_route = {
            (path, tuple(methods)): (handler, description)
            for path, handler, methods, description in bindings
        }

        self.assertIn(("/overview", ("GET",)), by_route)
        self.assertIn(("/qzone/post", ("GET",)), by_route)
        self.assertIn(("/qzone/post", ("POST",)), by_route)

        overview_handler = inspect.unwrap(by_route[("/overview", ("GET",))][0])
        qzone_get_handler = inspect.unwrap(by_route[("/qzone/post", ("GET",))][0])
        qzone_post_handler = inspect.unwrap(by_route[("/qzone/post", ("POST",))][0])
        self.assertEqual(overview_handler.__name__, "get_overview")
        self.assertEqual(qzone_get_handler.__name__, "get_qzone_detail")
        self.assertEqual(qzone_post_handler.__name__, "publish_qzone_post")

    def test_register_routes_reuses_bindings_with_astrbot_prefix(self) -> None:
        bindings = self.api.route_bindings()

        with patch.object(self.api, "route_bindings", return_value=bindings) as route_bindings:
            self.api.register_routes()

        route_bindings.assert_called_once_with()
        self.assertEqual(len(self.context.calls), len(bindings))
        self.assertEqual(
            self.context.calls,
            [
                (f"{PAGE_API_PREFIX}{path}", handler, methods, description)
                for path, handler, methods, description in bindings
            ],
        )
        registered_methods = {
            (route, tuple(methods)) for route, _handler, methods, _description in self.context.calls
        }
        self.assertIn((f"{PAGE_API_PREFIX}/overview", ("GET",)), registered_methods)
        self.assertIn((f"{PAGE_API_PREFIX}/qzone/post", ("GET",)), registered_methods)
        self.assertIn((f"{PAGE_API_PREFIX}/qzone/post", ("POST",)), registered_methods)


class PageAssetPrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_asset_prefix_follows_standalone_and_legacy_request_contexts(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        app = Quart(__name__)

        self.assertEqual(api._page_asset_prefix(), PAGE_API_PREFIX)

        async with app.test_request_context("/api/v1/overview"):
            self.assertEqual(api._page_asset_prefix(), "/api/v1")
            self.assertEqual(
                api._bookshelf_image_url(
                    "album 1",
                    data_root=Path.cwd(),
                    page_index=2,
                ),
                "/api/v1/bookshelf/image?album_id=album%201&page=2",
            )

        async with app.test_request_context(f"{PAGE_API_PREFIX}/overview"):
            self.assertEqual(api._page_asset_prefix(), PAGE_API_PREFIX)
            self.assertEqual(
                api._bookshelf_image_url(
                    "album 1",
                    data_root=Path.cwd(),
                    page_index=2,
                ),
                f"{PAGE_API_PREFIX}/bookshelf/image?album_id=album%201&page=2",
            )


if __name__ == "__main__":
    unittest.main()
