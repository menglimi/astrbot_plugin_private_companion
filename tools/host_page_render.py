"""Render one plugin view through whichever page service the host ships.

AstrBot moved its dashboard view entry points. Up to 4.28 the service exposes
``rewrite_plugin_page_html`` / ``rewrite_plugin_page_css`` and rewrites every
relative asset reference into an absolute ``/api/plugin/page/content/...`` URL.
From 4.29 the same HTML is handled by ``process_plugin_page_html``, the view is
served from a path-token URL (so relative references inherit the token by
themselves) and nothing is rewritten; ``build_plugin_page_view_content_path``
replaces ``build_plugin_page_content_path``.

The plugin declares support back to ``astrbot_version >= 4.27.2``, and the QA
tools that read a page must run against both shapes, so each call site picks the
branch the installed host actually provides instead of pinning one of them.
"""

from __future__ import annotations

DEFAULT_ENTRY = "index.html"


def render_html(service, html, plugin_name, page_name, file_name=DEFAULT_ENTRY,
                *, theme=None, extra_query_params=None):
    """Return the HTML the host would serve for one view entry."""
    process = getattr(service, "process_plugin_page_html", None)
    if callable(process):
        return process(html, theme=theme, extra_query_params=extra_query_params)
    rewrite = getattr(service, "rewrite_plugin_page_html", None)
    if callable(rewrite):
        return rewrite(html, plugin_name, page_name, file_name,
                       theme=theme, extra_query_params=extra_query_params)
    raise AttributeError("this host has no plugin view HTML entry point")


def render_css(service, text, plugin_name, page_name, file_name, *, extra_query_params=None):
    """Return the CSS the host would serve for one view asset.

    Hosts that serve a view from a path-token URL leave stylesheet URLs alone,
    so an unrewritten text is the correct answer there rather than a fallback.
    """
    rewrite = getattr(service, "rewrite_plugin_page_css", None)
    if callable(rewrite):
        return rewrite(text, plugin_name, page_name, file_name,
                       extra_query_params=extra_query_params)
    return text


def content_prefix(service, plugin_name, page_name):
    """Return the content URL prefix the host would publish for one view."""
    legacy = getattr(service, "build_plugin_page_content_path", None)
    if callable(legacy):
        return legacy(plugin_name, page_name, "")
    # The token URL carries the token as a path segment; without one the prefix
    # is still the same base, so trim the empty segment instead of keeping "//".
    return service.build_plugin_page_view_content_path(plugin_name, page_name, "").rstrip("/") + "/"
