from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "pages" / "companion-panel"


def _text(relative: str) -> str:
    return (PAGE_ROOT / relative).read_text(encoding="utf-8")


def test_home_room_is_a_native_companion_panel_tab() -> None:
    html = _text("index.html")
    script = _text("app.js")

    assert 'data-tab="home"' in html
    assert 'id="panel-home"' in html
    assert 'id="homeRoomRoot"' in html
    assert './css/home-room.css?v=20260915-voxel-beta1' in html
    assert './js/panels/home-room.js?v=20260915-voxel-beta1' in html
    assert 'tabName === "home"' in script
    assert 'renderHomeRoom();' in script
    assert 'tabName === "calendar" || tabName === "memory" || tabName === "home"' in script


def test_home_room_reads_companion_data_and_only_writes_memos_and_layout() -> None:
    script = _text("js/panels/home-room.js")

    for field in (
        "overview.plugin?.bot_name",
        "overview.daily_state",
        "overview.life_observation?.current_plan",
        "overview.daily_timeline?.segments",
        "state?.calendar?.today?.events",
        "daily_outfit",
        "image_data_url",
    ):
        assert field in script
    assert "context.fetchJson(endpoint)" in script
    assert 'context.postJson("/memo/update", payload)' in script
    assert 'context.postJson("/home-room/layout/update", { layout, _persona_id: persona })' in script
    assert script.count("context.postJson(") == 2
    assert "/chat" not in script
    assert "/send" not in script


def test_home_room_destinations_stay_inside_existing_workspaces() -> None:
    html = _text("index.html")
    script = _text("js/panels/home-room.js")

    assert 'id="homeRoomStations"' in html
    assert 'data-home-room-station="calendar"' in html
    assert 'data-home-room-station="wardrobe"' in html
    assert 'wardrobe: ["roleplay",' in script
    assert '[data-world-section="wardrobe"]' in script
    assert "creativeAvailable()" in script


def test_three_room_is_local_lazy_and_pauses_off_tab() -> None:
    html = _text("index.html")
    app = _text("app.js")
    controller = _text("js/panels/home-room.js")
    source = _text("js/panels/home-room-3d.source.js")
    bundle = PAGE_ROOT / "js" / "panels" / "home-room-3d.js"

    assert '<script src="./js/panels/home-room-3d.js' not in html
    assert 'loadOptionalClassicScript("./js/panels/home-room-3d.js?' in app
    assert 'homeRoom3d: "PrivateCompanionHomeRoom3D"' in app
    assert 'loadOptionalModule("homeRoom3d")' in controller
    assert 'setActive?.(tabName === "home")' in app
    assert 'import * as THREE from "../vendor/three.module.min.js";' in source
    assert 'import { OrbitControls } from "../vendor/OrbitControls.js";' in source
    assert "toggleTour" in source
    assert "requestAnimationFrame" in source
    assert "raycaster.intersectObjects" in source
    assert bundle.is_file()
    assert 100_000 < bundle.stat().st_size < 1_000_000


def test_home_room_persona_and_standalone_lifecycle() -> None:
    app = _text("app.js")
    standalone = (ROOT / "standalone_webui.py").read_text(encoding="utf-8")

    reset_section = app.split("function resetPersonaScopedPageState()", 1)[1].split(
        "async function selectPagePersona", 1
    )[0]
    assert "state.calendar = null;" in reset_section
    assert 'state.calendarMonth = "";' in reset_section
    assert "state.calendarRequestSeq += 1;" in reset_section
    assert "calendar: false" in reset_section
    assert "PrivateCompanionHomeRoom?.resetPersona?.()" in reset_section
    assert '"/js/<path:filename>"' in standalone


def test_home_room_assets_are_utf8_and_only_load_the_voxel_scene() -> None:
    for relative in (
        "css/home-room.css",
        "js/panels/home-room.js",
        "js/panels/home-room-3d.source.js",
    ):
        raw = (PAGE_ROOT / relative).read_bytes()
        assert raw.decode("utf-8")
        assert not raw.startswith(b"\xef\xbb\xbf")
    html = _text("index.html")
    script = _text("js/panels/home-room.js")
    assert 'data-home-room-scene-2d' not in html
    assert 'home-room-reference.jpg' not in html
    assert 'data-home-room-view' not in html
    assert 'pc_home_room_view_v1' not in script
    assert 'loadRoomScene();' in script
