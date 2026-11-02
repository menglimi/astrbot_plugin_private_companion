"""One page-rendering shim has to behave correctly against both host shapes.

The dashboard view service renamed its entry points: 4.28 and older expose
``rewrite_plugin_page_html`` / ``rewrite_plugin_page_css`` /
``build_plugin_page_content_path`` and rewrite relative assets into absolute
content URLs, while 4.29 serves a view from a path-token URL and only keeps
``process_plugin_page_html`` / ``build_plugin_page_view_content_path``. The
plugin supports back to 4.27.2, so every reader of a page needs both branches.
"""

import unittest

from tools.host_page_render import content_prefix, render_css, render_html


class LegacyHost:
    """Rewrites relative assets into ``/api/plugin/page/content/...``."""

    def rewrite_plugin_page_html(self, html, plugin_name, page_name, file_name,
                                 *, theme=None, extra_query_params=None):
        query = sorted((extra_query_params or {}).items())
        return f"legacy|{plugin_name}|{page_name}|{file_name}|{theme}|{query}|{html}"

    def rewrite_plugin_page_css(self, text, plugin_name, page_name, file_name,
                                *, extra_query_params=None):
        return f"legacy-css|{plugin_name}|{page_name}|{file_name}|{text}"

    def build_plugin_page_content_path(self, plugin_name, page_name, asset_path):
        return f"/api/plugin/page/content/{plugin_name}/{page_name}/{asset_path}"


class TokenHost:
    """Serves the view from a path-token URL, so assets stay relative."""

    def process_plugin_page_html(self, html, *, theme=None, extra_query_params=None):
        query = sorted((extra_query_params or {}).items())
        return f"token|{theme}|{query}|{html}"

    def build_plugin_page_view_content_path(self, plugin_name, page_name, token, asset_path=""):
        return f"/api/plugin/page/content/{plugin_name}/{page_name}/{token}/{asset_path}"


class HostPageRenderTests(unittest.TestCase):
    def test_the_legacy_shape_keeps_its_rewrite(self):
        service = LegacyHost()
        self.assertEqual(
            render_html(service, "<p/>", "plugin", "page", "index.html",
                        theme="light", extra_query_params={"asset_token": "t"}),
            "legacy|plugin|page|index.html|light|[('asset_token', 't')]|<p/>")
        self.assertEqual(render_css(service, "a{}", "plugin", "page", "a.css"),
                         "legacy-css|plugin|page|a.css|a{}")
        self.assertEqual(content_prefix(service, "plugin", "page"),
                         "/api/plugin/page/content/plugin/page/")

    def test_the_token_shape_uses_the_new_entry_points(self):
        service = TokenHost()
        self.assertEqual(render_html(service, "<p/>", "plugin", "page", theme="light"),
                         "token|light|[]|<p/>")
        # A stylesheet needs no rewrite when the view is served from a token URL:
        # relative references already resolve to the same token path.
        self.assertEqual(render_css(service, "a{}", "plugin", "page", "a.css"), "a{}")
        self.assertEqual(content_prefix(service, "plugin", "page"),
                         "/api/plugin/page/content/plugin/page/")

    def test_the_theme_and_query_reach_the_host_unchanged(self):
        service = TokenHost()
        self.assertEqual(
            render_html(service, "<p/>", "plugin", "page", theme="dark",
                        extra_query_params={"asset_token": "t"}),
            "token|dark|[('asset_token', 't')]|<p/>")

    def test_a_host_without_any_entry_point_says_so(self):
        with self.assertRaises(AttributeError):
            render_html(object(), "<p/>", "plugin", "page")


if __name__ == "__main__":
    unittest.main()
