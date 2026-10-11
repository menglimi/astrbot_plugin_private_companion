from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion import page_api
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


@pytest.mark.parametrize("backend", ["external_first", "anima_master"])
def test_backend_setting_is_preserved_by_normalization(backend):
    api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
    api._schema_bool_keys = lambda: set()
    assert api._normalize_setting_value("photo_generation_backend", backend) == backend


def api_with(image_api):
    api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
    api.plugin = SimpleNamespace(_image_companion_api=lambda: image_api)
    return api


@pytest.mark.asyncio
async def test_panel_config_save_uses_image_extension_and_reports_persistence(monkeypatch):
    updates = {"base_url": "http://comfyui:8188", "aspect": "workflow"}
    image_api = SimpleNamespace(update_comfyui_config=AsyncMock(return_value=updates))
    monkeypatch.setattr(page_api, "request", SimpleNamespace(get_json=AsyncMock(return_value={"config": updates})))
    result = await api_with(image_api).update_comfyui_config()
    assert result["success"] and result["data"]["config_saved"]
    assert result["data"]["comfyui"] == updates
    image_api.update_comfyui_config.assert_awaited_once_with(updates)


@pytest.mark.asyncio
async def test_panel_failed_save_and_missing_extension_are_visible(monkeypatch):
    monkeypatch.setattr(page_api, "request", SimpleNamespace(get_json=AsyncMock(return_value={"config": {}})))
    image_api = SimpleNamespace(update_comfyui_config=AsyncMock(side_effect=ValueError("save failed")))
    assert not (await api_with(image_api).update_comfyui_config())["success"]
    assert not (await api_with(None).update_comfyui_config())["success"]


@pytest.mark.asyncio
async def test_connection_and_workflow_checks_do_not_generate(monkeypatch):
    image_api = SimpleNamespace(test_comfyui_connection=AsyncMock(return_value={"ok": True}),
                                analyze_comfyui_workflow=AsyncMock(return_value={"mapping": {"fields": {}}}))
    api = api_with(image_api)
    assert (await api.test_comfyui_connection())["success"]
    monkeypatch.setattr(page_api, "request", SimpleNamespace(get_json=AsyncMock(return_value={"workflow": "portrait"})))
    assert (await api.inspect_comfyui_workflow())["success"]
    image_api.analyze_comfyui_workflow.assert_awaited_once_with(api.plugin, "portrait", use_model=False, save=True)


@pytest.mark.asyncio
async def test_daily_outfit_does_not_invent_an_explicit_size():
    class Harness(ProactiveMessageMixin):
        enable_photo_text_action = True
        def _photo_text_available(self): return True
        def _daily_outfit_schedule_text(self): return ""
        def _select_daily_outfit_profile(self, **kwargs): return {}
        def _build_daily_outfit_photo_prompt(self, *args, **kwargs): return "casual outfit portrait"
        def _build_daily_outfit_photo_prompt_sections(self, *args, **kwargs): return ()

    owner = Harness()
    owner._generate_photo_image = AsyncMock(return_value=("ComfyUI", "/outfit.png", "ok"))
    owner._record_daily_outfit_photo_result = AsyncMock(return_value={})
    await owner._ensure_daily_outfit_photo_unlocked({}, today="2030-01-01")
    request = owner._generate_photo_image.await_args.kwargs
    assert request["workflow_kind"] == "selfie" and request["session_key"] == "daily_outfit"
    assert not request.get("image_size")
