# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy

import pytest
import pytest_asyncio
from quart import Quart

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.utils import plugin_kv_store
from astrbot.core.utils.plugin_kv_store import PluginKVStoreMixin
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot_plugin_private_companion.home_room_layout import normalize_room_layout
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


LAYOUT = {
    "version": 1, "houseStyle": "washitsu",
    "items": {"desk": {"x": -1.5, "y": 0, "z": -2.5, "rotation": 45, "style": "rattan", "model": "standard", "surface": "floor"}},
}


class Host(PluginKVStoreMixin):
    plugin_id = "room-layout-test"

    def __init__(self):
        self.scope = ContextVar("room-layout-persona", default="")

    def _primary_persona_id(self):
        return "primary"

    def _active_persona_scope(self):
        return self.scope.get()

    def _persona_config_profile_ids(self):
        return ["primary", "第二人格"]

    def _activate_persona_id(self, persona_id, **_kwargs):
        return self.scope.set(persona_id)

    def _deactivate_persona_for_event(self, token):
        self.scope.reset(token)


@pytest_asyncio.fixture
async def room_api(tmp_path, monkeypatch):
    database = SQLiteDatabase(str(tmp_path / "preferences.db"))
    await database.initialize()
    # Use AstrBot's real preference transactions without starting its scheduler.
    preferences = SharedPreferences.__new__(SharedPreferences)
    preferences.db_helper = database
    monkeypatch.setattr(plugin_kv_store, "sp", preferences)
    plugin = Host()
    api = PrivateCompanionPageApi(plugin)
    app = Quart(__name__)
    for path, handler, methods, _description in api.route_bindings():
        if path in ("/home-room/layout", "/home-room/layout/update"):
            app.add_url_rule(path, path, handler, methods=methods)
    try:
        yield app.test_client(), plugin, database
    finally:
        await database.engine.dispose()


@pytest.mark.asyncio
async def test_layout_round_trip_after_database_reopen_and_persona_isolation(room_api):
    client, plugin, database = room_api
    response = await client.get("/home-room/layout")
    assert (await response.get_json())["data"] == {"layout": None}
    saved = await client.post("/home-room/layout/update", json={"layout": LAYOUT, "_persona_id": "第二人格"})
    assert saved.status_code == 200
    assert (await saved.get_json())["data"] == {"saved": True, "layout": LAYOUT}
    # Drop all connections; retrieval must come from committed SQLite storage.
    await database.engine.dispose()
    restored = await client.get("/home-room/layout", query_string={"_persona_id": "第二人格"})
    assert (await restored.get_json())["data"]["layout"] == LAYOUT
    primary = await client.get("/home-room/layout")
    assert (await primary.get_json())["data"]["layout"] is None
    unknown = await client.get("/home-room/layout?_persona_id=not-a-persona")
    assert (await unknown.get_json())["data"]["layout"] is None
    assert plugin._active_persona_scope() == ""


@pytest.mark.asyncio
async def test_failed_transaction_never_returns_success_or_replaces_saved_layout(room_api, monkeypatch):
    client, _plugin, database = room_api
    await client.post("/home-room/layout/update", json={"layout": LAYOUT})
    async def broken(*_args):
        raise OSError("private-internal-path")
    monkeypatch.setattr(database, "insert_preference_or_update", broken)
    changed = {**LAYOUT, "houseStyle": "cabin"}
    response = await client.post("/home-room/layout/update", json={"layout": changed})
    assert response.status_code == 503
    assert "private-internal-path" not in (await response.get_data(as_text=True))
    assert (await response.get_json())["success"] is False
    restored = await client.get("/home-room/layout")
    assert (await restored.get_json())["data"]["layout"] == LAYOUT


@pytest.mark.asyncio
async def test_save_acknowledgement_waits_for_database_commit(room_api, monkeypatch):
    client, plugin, _database = room_api
    started, release = asyncio.Event(), asyncio.Event()
    original = plugin.put_kv_data
    async def pending(key, value):
        started.set()
        await release.wait()
        await original(key, value)
    monkeypatch.setattr(plugin, "put_kv_data", pending)
    task = asyncio.create_task(client.post("/home-room/layout/update", json={"layout": LAYOUT}))
    await asyncio.wait_for(started.wait(), 3)
    assert not task.done()
    release.set()
    assert (await asyncio.wait_for(task, 3)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", [None, [], {"version": 2, "items": {}}, {"version": True, "items": {}}, {"version": 1, "items": {"desk": {"x": "bad"}}}])
async def test_invalid_layout_does_not_overwrite_saved_data(room_api, layout):
    client, _plugin, _database = room_api
    await client.post("/home-room/layout/update", json={"layout": LAYOUT})
    response = await client.post("/home-room/layout/update", json={"layout": layout})
    assert response.status_code == 400
    restored = await client.get("/home-room/layout")
    assert (await restored.get_json())["data"]["layout"] == LAYOUT


def test_legacy_layouts_and_new_catalog_ids_are_preserved_without_business_data():
    legacy = {"version": 1, "items": {"future-furniture": {"x": 1, "style": "future-style"}}}
    assert normalize_room_layout(legacy) == legacy
    extra = deepcopy(LAYOUT)
    extra["messages"] = ["not-room-data"]
    extra["items"]["desk"]["prompt"] = "not-room-data"
    assert normalize_room_layout(extra) == LAYOUT
    for number in (float("inf"), float("nan"), True):
        extra["items"]["desk"]["x"] = number
        with pytest.raises(ValueError):
            normalize_room_layout(extra)
