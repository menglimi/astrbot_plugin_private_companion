from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "pages" / "companion-panel"


def _text(relative: str) -> str:
    return (PAGE_ROOT / relative).read_text(encoding="utf-8")


def test_heavy_panel_scripts_are_lazy_classic_scripts() -> None:
    html = _text("index.html")
    script = _text("app.js")

    assert '<script src="./js/panels/provider-tree.js' not in html
    assert '<script src="./js/panels/qzone-panel.js' not in html
    assert 'loadOptionalClassicScript("./js/panels/provider-tree.js?' in script
    assert 'loadOptionalClassicScript("./js/panels/qzone-panel.js?' in script
    assert "import(\"./js/panels/qzone-panel.js?" not in script
    assert 'providerTree: "PrivateCompanionProviderTree"' in script
    assert 'qzonePanel: "PrivateCompanionQzonePanel"' in script


def test_page_waits_for_bridge_and_keeps_debug_http_fallback() -> None:
    script = _text("app.js")

    assert "async function getReadyPageBridge" in script
    assert "bridge.ready()" in script
    assert "if (isHttpApiMode()) return null;" in script
    assert "const bridge = await getReadyPageBridge();" in script
    assert "void bootstrapPage();" in script


def test_page_supports_standalone_http_api_without_replacing_bridge() -> None:
    script = _text("app.js")

    assert 'const HTTP_API = "/astrbot_plugin_private_companion/page";' in script
    assert 'const STANDALONE_HTTP_API = "/api/v1";' in script
    assert "window.__PRIVATE_COMPANION_STANDALONE__" in script
    assert 'String(candidate.mode || "").toLowerCase() === "standalone"' in script
    assert 'meta[name="${STANDALONE_MODE_META}"]' in script
    assert "if (standaloneHttpModeDetected !== undefined)" in script
    assert "payload = await bridgeRequest(bridge, path, method, options.body);" in script
    assert "fetch(httpApiRequestUrl(path)" in script
    assert "function pageApiAssetUrl(value)" in script
    assert "pageApiAssetUrl(asset.preview_endpoint)" in script
    assert 'credentials: "same-origin"' in script
    assert 'get("api_token")' not in script
    assert 'get("token")' not in script


def test_standalone_login_uses_cookie_session_without_persisting_token() -> None:
    script = _text("standalone.js")

    assert "if (!markedStandalone) return;" in script
    assert 'authRequest("/auth/status")' in script
    assert 'authRequest("/auth/login"' in script
    assert 'authRequest("/auth/logout"' in script
    assert "JSON.stringify({ token })" in script
    assert 'credentials: "same-origin"' in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "URLSearchParams" not in script
    assert "scheduleExpiryCheck(status.expires_at)" in script


def test_get_requests_are_deduplicated_only_while_in_flight() -> None:
    script = _text("app.js")

    assert "const inFlightGetRequests = new Map();" in script
    assert 'method === "GET" && dedupe' in script
    assert "inFlightGetRequests.has(requestKey)" in script
    assert "inFlightGetRequests.delete(requestKey)" in script
    assert "const scoped = scopePagePersonaRequest" in script


def test_dashboard_defers_large_user_and_group_lists() -> None:
    script = _text("app.js")

    assert "function scheduleUserGroupPrefetch" in script
    assert "scheduleUserGroupPrefetch(() =>" in script
    assert 'if (tabName !== "dashboard")' in script
    assert "cancelUserGroupPrefetch();" in script


def test_cancelled_view_transitions_do_not_leak_page_errors() -> None:
    script = _text("app.js")

    assert "function watchTabTransition(transition)" in script
    assert "transition.ready?.catch(() => {})" in script
    assert "transition.updateCallbackDone?.catch(() => {})" in script
    assert "transition.finished.then(cleanup, cleanup)" in script
    assert "transition.finished.finally" not in script


def test_responsive_tail_contains_narrow_screen_safety_rules() -> None:
    css = _text("css/polish.css")
    marker = "/* Responsive containment and sticky-stack safety. Keep this layer last. */"
    tail = css.split(marker, 1)[1]

    assert "overflow-x: clip;" in tail
    assert "min-height: 100dvh;" in tail
    assert "min-width: 0;" in tail
    assert "env(safe-area-inset-left)" in tail
    assert "@media (max-width: 900px)" in tail
    assert "@media (max-width: 760px)" in tail
    assert "@media (max-width: 480px)" in tail
    assert ".image-cache-layout," in tail
    assert ".bookcase-layout" in tail
    assert "./css/polish.css?v=20260810-responsive-containment-v1" in _text("index.html")
    assert ".exp-card-toggle input," in tail
    assert ".feature-switch-item input" in tail
    assert "width: 1px;" in tail


def test_world_workspace_collapses_after_polish_overrides() -> None:
    css = _text("css/polish.css")
    tail = css.split("/* Responsive containment and sticky-stack safety. Keep this layer last. */", 1)[1]

    assert "container-name: world-page;" in css
    assert "@container world-page (max-width: 900px)" in tail
    assert "grid-template-columns: minmax(0, 1fr);" in tail
    assert "position: static;" in tail
    assert "flex: 1 0 128px;" in tail
    assert "world=20260811-responsive-v2" in _text("index.html")


def test_ascii_and_utf8_page_mirrors_match_after_optimization() -> None:
    ascii_root = ROOT / "pages" / "companion-panel"
    utf8_root = ROOT / "pages" / "陪伴面板"
    for relative in ("index.html", "app.js", "standalone.js", "css/polish.css"):
        assert (ascii_root / relative).read_bytes() == (utf8_root / relative).read_bytes()
