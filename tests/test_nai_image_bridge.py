from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from astrbot_plugin_private_companion.nai_image_bridge import NAIImageBridgeMixin


class _BridgeHarness(NAIImageBridgeMixin):
    context = None
    photo_generation_backend = "nai"


@pytest.mark.asyncio
async def test_nai_image_bridge_preserves_result_shape_and_metadata() -> None:
    received: dict[str, object] = {}

    class Api:
        async def generate_for_companion(self, owner, request):
            received.update(request)
            assert isinstance(owner, _BridgeHarness)
            return {
                "handled": True,
                "backend": "NAI 生图",
                "image_path": "C:/output.png",
                "note": "ok",
                "metadata": {"trace": "nai-1"},
            }

    harness = _BridgeHarness()
    module_name = "astrbot_plugin_nai_image.main"
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = SimpleNamespace(get_nai_image_api=lambda: Api())
    try:
        result = await harness._nai_image_generate(
            workflow_kind="text2img",
            prompt_text="a rainy window",
            session_key="test",
        )
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous

    assert result == ("NAI 生图", "C:/output.png", "ok")
    assert received["workflow_kind"] == "text2img"
    assert harness._nai_image_last_metadata() == {"trace": "nai-1"}


@pytest.mark.asyncio
async def test_nai_image_bridge_returns_install_hint_without_plugin() -> None:
    harness = _BridgeHarness()
    result = await harness._nai_image_generate(workflow_kind="selfie", prompt_text="test")
    assert result[0] == "NAI 生图"
    assert "astrbot_plugin_nai_image" in result[2]


def test_nai_image_status_is_unavailable_without_plugin() -> None:
    harness = _BridgeHarness()
    harness._nai_image_api = lambda: None

    status = harness._nai_image_status()

    assert status["installed"] is False
    assert status["available"] is False
    assert harness._nai_image_available() is False


def test_nai_image_selected_follows_configured_backend() -> None:
    harness = _BridgeHarness()
    assert harness._nai_image_selected() is True
    harness.photo_generation_backend = "external"
    assert harness._nai_image_selected() is False


def test_active_unified_nai_route_can_claim_selection_without_double_call() -> None:
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: SimpleNamespace(
        claims_model_profile=lambda profile, **kwargs: (
            profile == "nai" and kwargs.get("operation", "selfie") == "selfie"
        )
    )

    assert harness._nai_image_selected("selfie") is False
    assert harness._nai_image_selected("text2img") is True


def test_failed_unified_route_ownership_check_keeps_official_direct_nai() -> None:
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: SimpleNamespace(
        claims_model_profile=lambda _profile: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert harness._nai_image_selected() is True


@pytest.mark.asyncio
async def test_nai_image_status_and_maintenance_delegate_to_external_api() -> None:
    calls: list[object] = []

    class Api:
        def capability_status(self, owner):
            calls.append(owner)
            return {
                "installed": True,
                "enabled": True,
                "available": True,
                "backends": {"nai": True},
            }

        async def maintenance(self, owner):
            calls.append(("maintenance", owner))
            return {"removed_files": 2}

    harness = _BridgeHarness()
    harness._nai_image_api = lambda: Api()

    assert harness._nai_image_available() is True
    assert await harness._nai_image_maintenance() == {"removed_files": 2}
    assert calls[-1] == ("maintenance", harness)
