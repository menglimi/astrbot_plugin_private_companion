import asyncio
from pathlib import Path

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

from tests.module_source_index import page_api_source_text


ROOT = Path(__file__).resolve().parents[1]
PANEL_ROOTS = [ROOT / "pages" / "companion-panel", ROOT / "pages" / "陪伴面板"]


def test_creative_image_and_reality_are_conditional_companion_workspaces() -> None:
    for panel_root in PANEL_ROOTS:
        html = (panel_root / "index.html").read_text(encoding="utf-8")
        script = (panel_root / "app.js").read_text(encoding="utf-8")
        css = (panel_root / "app.css").read_text(encoding="utf-8")

        assert 'data-tab="creative"' in html
        assert 'data-tab="image"' in html
        assert 'data-tab="bookshelf"' not in html
        assert 'data-tab="qzone"' not in html
        assert 'id="panel-creative"' in html
        assert 'id="panel-image"' in html
        assert 'id="panel-reality"' in html
        assert html.index('data-tab="experimental"') < html.index('data-tab="reality"')

        assert 'creativeTab.hidden = !creativeInstalled' in script
        assert 'imageRuntimeTab.hidden = !imageInstalled' in script
        assert 'realityTab.hidden = !realityInstalled' in script
        assert 'qzone.classList.remove("panel")' in script
        assert 'creative.appendChild(qzone)' in script
        assert ".annotations .tab[hidden]" in css
        assert ".layout > .panel[hidden]" in css
        assert 'fetchJson("/extensions/image/status")' in script
        assert 'openModelConfigSection("image")' in script


def test_image_workspace_is_served_only_through_companion_page_api() -> None:
    source = page_api_source_text(ROOT)

    assert '("/extensions/image/status", self.get_image_extension_status' in source
    assert "async def get_image_extension_status" in source


def test_troubleshooting_redraw_releases_current_test_button() -> None:
    for panel_root in PANEL_ROOTS:
        script = (panel_root / "app.js").read_text(encoding="utf-8")

        assert 'document.querySelectorAll("[data-troubleshooting-test]")' in script
        assert "button.dataset.troubleshootingTest === testType" in script
        assert "setActionBusy(button, false)" in script


def test_image_workspace_api_proxies_extension_status() -> None:
    expected = {
        "installed": True,
        "enabled": True,
        "available": True,
        "state": "managed",
        "generation_count": 3,
    }
    extension = type("ImageExtension", (), {"status": lambda self: dict(expected)})()
    plugin = type("Plugin", (), {"_image_companion_api": lambda self: extension})()

    result = asyncio.run(PrivateCompanionPageApi(plugin).get_image_extension_status())

    assert result["success"] is True
    assert result["data"]["state"] == "managed"
    assert result["data"]["generation_count"] == 3


def test_image_workspace_api_reports_contract_mismatch_as_unavailable() -> None:
    extension = type("ImageExtension", (), {"status": lambda self: {"enabled": True, "available": True}})()
    plugin = type("Plugin", (), {
        "_image_companion_api": lambda self: extension,
        "_image_companion_contract": lambda self, **_kwargs: (
            "incompatible", extension, 0, "descriptor_method_missing"
        ),
    })()

    result = asyncio.run(PrivateCompanionPageApi(plugin).get_image_extension_status())

    assert result["data"]["available"] is False
    assert result["data"]["state"] == "incompatible"
    assert result["data"]["reason"] == "descriptor_method_missing"
    assert result["data"]["companion_contract"]["mode"] == "incompatible"


def test_reality_workspace_exposes_mobile_gateway_without_owning_implementation() -> None:
    script = (PANEL_ROOTS[0] / "app.js").read_text(encoding="utf-8")
    css = (PANEL_ROOTS[0] / "app.css").read_text(encoding="utf-8")

    assert 'data-reality-mobile-config' in script
    assert 'action: "save_global_config"' in script
    assert 'postJson("/reality-touch/update"' in script
    assert "function renderRealityTouchPage()" in script
    assert 'const canToggle = Boolean(data && !state.realityTouchLoading && !state.realityTouchError)' in script
    assert 'name="mobile_activity_enabled"' in script
    assert 'name="mobile_activity_ttl_seconds"' in script
    assert 'name="mobile_telemetry_enabled"' in script
    assert 'name="mobile_telemetry_ttl_seconds"' in script
    assert 'activity_enabled: field("mobile_activity_enabled")' in script
    assert 'activity_ttl_seconds: Number(' in script
    assert 'mobile.telemetry_enabled === true' in script
    assert 'mobile.telemetry_ttl_seconds || 3600' in script
    assert 'function renderRealityTouchHomeHealthPanel()' in script
    assert 'function renderRealityTouchMobileDataPanel()' in script
    assert 'data-reality-mobile-data-config' in script
    assert '保存手机数据设置' in script
    assert 'root.querySelectorAll("[data-reality-mobile-config], [data-reality-mobile-data-config]")' in script
    assert 'mobile_observations' in script
    assert 'data-reality-mobile-observation-user' in script
    assert '暂无可用摘要' in script
    assert '最近测量值' in script
    assert '刷新接收状态' in script
    assert 'data-reality-home-action' in script
    assert 'action: "external_reality_request"' in script
    assert 'data-reality-home-config' in script
    assert 'data-reality-health-config' in script
    assert 'name="home_base_url"' in script
    assert 'name="home_access_token"' in script
    assert 'name="health_enabled"' in script
    assert 'action_result' in script
    assert 'href="#reality-mobile-data"' in script
    assert 'href="#reality-home-health"' in script
    assert "mihomeLogin.detail || (mihomeLogin.status === \"error\" ? mihomeAuth.last_login_error : \"\")" in script
    assert "mihomeArc4Failure" in script
    assert "pycryptodome==3.23.0" in script
    assert "syncRealityTouchOverviewState(result)" in script
    assert ".reality-global-toggle input:checked + .feature-toggle-visual" in css
    assert ".reality-global-toggle input:checked + .feature-toggle-visual::after" in css
    assert '"enable_experimental_bluetooth_wakeup",\n  "enable_daily_case_review_experiment"' not in script


def test_reality_feature_flag_uses_external_plugin_state() -> None:
    extension = type("RealityExtension", (), {"status": lambda self: {"enabled": True}})()
    plugin = type("Plugin", (), {"_reality_companion_api": lambda self: extension})()
    api = PrivateCompanionPageApi(plugin)
    api._screen_companion_available = lambda: False

    assert api._feature_flags()["enable_experimental_bluetooth_wakeup"] is True


def test_reality_mihome_snapshot_compatibility_normalizes_legacy_action_payload() -> None:
    legacy = {
        "mihome": {
            "auth": {"logged_in": False},
            "login": {"status": "starting"},
        }
    }
    normalized = PrivateCompanionPageApi._normalize_reality_touch_snapshot(legacy)

    assert normalized["mihome"]["available"] is True

    unavailable = {"mihome": {"available": False, "auth": {}}}
    assert PrivateCompanionPageApi._normalize_reality_touch_snapshot(unavailable)["mihome"]["available"] is False
