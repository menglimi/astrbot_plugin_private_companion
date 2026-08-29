from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_image_companion.image_runtime import ImageGenerationRuntime
from astrbot_plugin_image_companion.photo_reference_catalog import (
    PhotoReference as ImagePhotoReference,
)
from astrbot_plugin_private_companion.image_companion_bridge import (
    ImageCompanionBridgeMixin,
)
from astrbot_plugin_private_companion.photo_reference_catalog import (
    load_catalog as load_private_catalog,
)


class _BridgeHarness(ImageCompanionBridgeMixin):
    context = None


def _reference_catalog(
    *,
    persona_source: str = "persona.png",
    library_source: str = "sleepwear.png",
) -> list[dict[str, object]]:
    return [
        {
            "id": "persona",
            "kind": "persona",
            "source": persona_source,
            "note": "identity",
            "reference_roles": ["identity"],
            "outfit_category": "",
            "outfit_lock_default": False,
            "scene_categories": [],
            "time_categories": [],
            "preferred_preset": "",
            "metadata_source": "configured",
            "editor_intent": None,
            "excluded_scene_categories": [],
            "excluded_time_categories": [],
            "selection_eligibility": "fallback_identity_only",
        },
        {
            "id": "sleepwear",
            "kind": "library",
            "source": library_source,
            "note": "sleepwear",
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "sleepwear",
            "outfit_lock_default": True,
            "scene_categories": ["home", "bedroom"],
            "time_categories": ["night", "bedtime"],
            "preferred_preset": "",
            "metadata_source": "configured",
            "editor_intent": None,
            "excluded_scene_categories": [],
            "excluded_time_categories": [],
            "selection_eligibility": "matching_only",
        },
    ]


def _image_service(
    data_dir: Path,
    *,
    settings: dict[str, object] | None = None,
    reuse_private_companion_assets: bool = True,
) -> SimpleNamespace:
    configured = dict(settings or {})
    return SimpleNamespace(
        data_dir=str(data_dir),
        reuse_private_companion_assets=reuse_private_companion_assets,
        image_data_lock=asyncio.Lock(),
        image_setting=lambda name, default: configured.get(name, default),
        image_data_for=lambda _owner: {},
    )


def _isolate_reference_candidates(runtime, monkeypatch) -> None:
    for name in (
        "_photo_reference_role_asset_candidates",
        "_photo_reference_relation_asset_candidates",
        "_photo_reference_knowledge_asset_candidates",
    ):
        monkeypatch.setattr(runtime, name, lambda **_kwargs: [])


@pytest.mark.asyncio
async def test_split_runtime_accepts_private_companion_reference_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_data_dir = tmp_path / "private"
    image_data_dir = tmp_path / "image"
    private_data_dir.mkdir()
    image_data_dir.mkdir()
    (private_data_dir / "persona.png").write_bytes(b"png")
    (private_data_dir / "sleepwear.png").write_bytes(b"png")

    raw_catalog = _reference_catalog()
    owner_catalog = load_private_catalog(
        raw_catalog,
        catalog_version=2,
    ).references
    assert all(not isinstance(item, ImagePhotoReference) for item in owner_catalog)

    service = _image_service(image_data_dir)
    owner = SimpleNamespace(
        context=None,
        data_dir=str(private_data_dir),
        enable_photo_reference_image=True,
        photo_reference_catalog_version=2,
        photo_reference_catalog_user_cleared=False,
        photo_reference_catalog=owner_catalog,
    )
    runtime = ImageGenerationRuntime(service, owner)

    _isolate_reference_candidates(runtime, monkeypatch)

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )
    by_id = {item["id"]: item for item in candidates}

    assert set(by_id) == {"persona", "sleepwear"}
    assert (
        Path(by_id["persona"]["path"]) == (private_data_dir / "persona.png").resolve()
    )
    assert by_id["persona"]["reference_roles"] == ["identity"]
    assert (
        Path(by_id["sleepwear"]["path"])
        == (private_data_dir / "sleepwear.png").resolve()
    )
    assert by_id["sleepwear"]["reference_roles"] == ["identity", "outfit"]
    assert by_id["sleepwear"]["outfit_category"] == "sleepwear"
    assert by_id["sleepwear"]["outfit_lock_default"] is True


@pytest.mark.asyncio
async def test_split_runtime_accepts_image_service_reference_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image_data_dir = tmp_path / "image"
    private_data_dir = tmp_path / "private"
    image_data_dir.mkdir()
    private_data_dir.mkdir()
    (image_data_dir / "persona.png").write_bytes(b"png")
    (image_data_dir / "sleepwear.png").write_bytes(b"png")

    service = _image_service(
        image_data_dir,
        settings={"photo_reference_catalog": _reference_catalog()},
        reuse_private_companion_assets=True,
    )
    owner = SimpleNamespace(
        context=None,
        data_dir=str(private_data_dir),
        enable_photo_reference_image=True,
        photo_reference_catalog_version=2,
        photo_reference_catalog=_reference_catalog(
            persona_source="owner-persona.png",
            library_source="owner-sleepwear.png",
        ),
        photo_reference_catalog_user_cleared=False,
    )
    runtime = ImageGenerationRuntime(service, owner)
    _isolate_reference_candidates(runtime, monkeypatch)

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )
    by_id = {item["id"]: item for item in candidates}

    assert all(
        isinstance(item, ImagePhotoReference)
        for item in runtime.photo_reference_catalog
    )
    assert Path(runtime.data_dir) == private_data_dir
    assert set(by_id) == {"persona", "sleepwear"}
    assert Path(by_id["persona"]["path"]) == (image_data_dir / "persona.png").resolve()
    assert (
        Path(by_id["sleepwear"]["path"]) == (image_data_dir / "sleepwear.png").resolve()
    )


@pytest.mark.asyncio
async def test_split_runtime_preserves_owner_user_cleared_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_data_dir = tmp_path / "private"
    image_data_dir = tmp_path / "image"
    private_data_dir.mkdir()
    image_data_dir.mkdir()
    legacy_persona = private_data_dir / "legacy-persona.png"
    legacy_persona.write_bytes(b"png")

    runtime = ImageGenerationRuntime(
        _image_service(image_data_dir),
        SimpleNamespace(
            context=None,
            data_dir=str(private_data_dir),
            enable_photo_reference_image=True,
            photo_reference_catalog_version=2,
            photo_reference_catalog=(),
            photo_reference_catalog_user_cleared=True,
            photo_persona_reference_image_path=str(legacy_persona),
            photo_reference_library=[],
        ),
    )
    _isolate_reference_candidates(runtime, monkeypatch)

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )

    assert candidates == []


@pytest.mark.asyncio
async def test_split_runtime_preserves_legacy_fallback_without_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_data_dir = tmp_path / "private"
    image_data_dir = tmp_path / "image"
    private_data_dir.mkdir()
    image_data_dir.mkdir()
    legacy_persona = private_data_dir / "legacy-persona.png"
    legacy_persona.write_bytes(b"png")

    runtime = ImageGenerationRuntime(
        _image_service(image_data_dir),
        SimpleNamespace(
            context=None,
            data_dir=str(private_data_dir),
            enable_photo_reference_image=True,
            photo_reference_catalog=None,
            photo_persona_reference_image_path=str(legacy_persona),
            photo_reference_library=[],
        ),
    )
    _isolate_reference_candidates(runtime, monkeypatch)

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )

    assert len(candidates) == 1
    assert candidates[0]["id"] == "persona"
    assert Path(candidates[0]["path"]) == legacy_persona.resolve()


@pytest.mark.asyncio
async def test_split_runtime_serializes_remote_catalog_before_owner_writeback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_data_dir = tmp_path / "private"
    image_data_dir = tmp_path / "image"
    private_data_dir.mkdir()
    image_data_dir.mkdir()
    (private_data_dir / "persona.png").write_bytes(b"png")
    stable_path = private_data_dir / "cached-sleepwear.png"
    stable_path.write_bytes(b"png")
    owner_catalog = load_private_catalog(
        _reference_catalog(library_source="https://example.invalid/sleepwear.png"),
        catalog_version=2,
    ).references
    received: list[object] = []

    async def save_catalog(payload) -> bool:
        received.append(payload)
        return True

    owner = SimpleNamespace(
        context=None,
        data_dir=str(private_data_dir),
        enable_photo_reference_image=True,
        photo_reference_catalog_version=2,
        photo_reference_catalog_user_cleared=False,
        photo_reference_catalog=owner_catalog,
        _set_photo_reference_catalog_config=save_catalog,
    )
    runtime = ImageGenerationRuntime(_image_service(image_data_dir), owner)
    _isolate_reference_candidates(runtime, monkeypatch)

    async def resolve_remote(_source: str, *, stem: str) -> str:
        assert stem == "sleepwear"
        return str(stable_path)

    monkeypatch.setattr(
        runtime,
        "_photo_reference_source_to_stable_path",
        resolve_remote,
        raising=False,
    )

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )

    assert len(received) == 1
    assert isinstance(received[0], list)
    assert all(isinstance(item, dict) for item in received[0])
    assert all(not isinstance(item, ImagePhotoReference) for item in received[0])
    persisted = load_private_catalog(received[0], catalog_version=2).references
    persisted_sleepwear = next(item for item in persisted if item.id == "sleepwear")
    assert persisted_sleepwear.source == str(stable_path)
    candidate = next(item for item in candidates if item["id"] == "sleepwear")
    assert Path(candidate["path"]) == stable_path.resolve()


@pytest.mark.asyncio
async def test_split_runtime_does_not_write_service_catalog_to_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_data_dir = tmp_path / "private"
    image_data_dir = tmp_path / "image"
    private_data_dir.mkdir()
    image_data_dir.mkdir()
    image_reference_dir = image_data_dir / "photo_reference_images"
    image_reference_dir.mkdir()
    stable_persona_path = image_reference_dir / "cached-persona.png"
    stable_persona_path.write_bytes(b"png")
    stable_path = image_reference_dir / "cached-sleepwear.png"
    stable_path.write_bytes(b"png")
    owner_writes: list[object] = []
    service_writes: list[object] = []
    resolve_calls: list[str] = []

    async def save_owner_catalog(payload) -> bool:
        owner_writes.append(("catalog", payload))
        return True

    async def save_owner_persona(path) -> bool:
        owner_writes.append(("persona", path))
        return True

    async def persist_remote(
        _source: str,
        target_dir: Path,
        stem: str,
        **_kwargs,
    ) -> str:
        resolve_calls.append(stem)
        assert Path(target_dir).resolve() == image_reference_dir.resolve()
        return str(
            stable_persona_path
            if stem.startswith("config_url_reference")
            else stable_path
        )

    service = _image_service(
        image_data_dir,
        settings={
            "photo_reference_catalog": _reference_catalog(
                persona_source="https://example.invalid/persona.png",
                library_source="https://example.invalid/sleepwear.png",
            )
        },
        reuse_private_companion_assets=False,
    )

    async def save_service_catalog(payload) -> bool:
        service_writes.append(("catalog", payload))
        return True

    async def save_service_persona(path) -> bool:
        service_writes.append(("persona", path))
        return True

    service._set_photo_reference_catalog_config = save_service_catalog
    service._set_photo_reference_config_path = save_service_persona
    owner = SimpleNamespace(
        context=None,
        data_dir=str(private_data_dir),
        enable_photo_reference_image=True,
        photo_reference_catalog_version=2,
        photo_reference_catalog_user_cleared=False,
        photo_reference_catalog=None,
        _set_photo_reference_catalog_config=save_owner_catalog,
        _set_photo_reference_config_path=save_owner_persona,
        _persist_private_remote_image_source=persist_remote,
    )
    runtime = ImageGenerationRuntime(service, owner)
    _isolate_reference_candidates(runtime, monkeypatch)

    candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )
    cached_candidates = await runtime._photo_reference_candidates_async(
        allow_daily_outfit=False,
    )

    assert owner_writes == []
    assert [kind for kind, _payload in service_writes] == ["catalog", "persona"]
    catalog_payload = service_writes[0][1]
    persisted = {
        item["id"]: item for item in catalog_payload if isinstance(item, dict)
    }
    assert persisted["sleepwear"]["source"] == str(stable_path)
    assert service_writes[1] == ("persona", str(stable_persona_path))
    assert resolve_calls == ["sleepwear_remote", "config_url_reference_remote"]
    persona_candidate = next(item for item in candidates if item["id"] == "persona")
    assert Path(persona_candidate["path"]) == stable_persona_path.resolve()
    candidate = next(item for item in candidates if item["id"] == "sleepwear")
    assert Path(candidate["path"]) == stable_path.resolve()
    assert {item["id"] for item in cached_candidates} == {"persona", "sleepwear"}


@pytest.mark.asyncio
async def test_image_companion_bridge_preserves_legacy_result_shape() -> None:
    received: dict[str, object] = {}

    class Api:
        async def generate_for_companion(self, owner, request):
            received.update(request)
            assert isinstance(owner, _BridgeHarness)
            return {
                "handled": True,
                "backend": "独立后端",
                "image_path": "C:/output.png",
                "note": "ok",
            }

    module_name = "astrbot_plugin_image_companion.main"
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = SimpleNamespace(get_image_companion_api=lambda: Api())
    try:
        result = await _BridgeHarness()._image_companion_generate(
            workflow_kind="selfie",
            prompt_text="take a picture",
            session_key="test",
        )
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous

    assert result == ("独立后端", "C:/output.png", "ok")
    assert received["workflow_kind"] == "selfie"


@pytest.mark.asyncio
async def test_split_runtime_does_not_call_owner_legacy_executor(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Service:
        data_dir = "C:/image-companion"
        image_data_lock = __import__("asyncio").Lock()

        @staticmethod
        def image_setting(_name, default):
            return default

        @staticmethod
        def image_data_for(_owner):
            return {}

    class Owner:
        context = None
        data_dir = "C:/private-companion"

        async def _generate_photo_image_legacy(self, **_kwargs):
            raise AssertionError(
                "split runtime must not call the owner's legacy executor"
            )

    async def split_executor(self, **kwargs):
        calls.append(kwargs)
        return "独立后端", "C:/output.png", "ok"

    monkeypatch.setattr(
        ImageGenerationRuntime, "_generate_photo_image_legacy", split_executor
    )
    runtime = ImageGenerationRuntime(Service(), Owner())
    assert await runtime.generate(
        {"workflow_kind": "selfie", "prompt_text": "test", "session_key": "umo"}
    ) == (
        "独立后端",
        "C:/output.png",
        "ok",
    )
    assert calls == [
        {"workflow_kind": "selfie", "prompt_text": "test", "session_key": "umo"}
    ]


@pytest.mark.asyncio
async def test_production_image_bridge_returns_external_plugin_message_without_fallback() -> (
    None
):
    class PrivateCompanionPlugin(ImageCompanionBridgeMixin):
        context = None

        async def _generate_photo_image_legacy(self, **_kwargs):
            raise AssertionError(
                "production host must not invoke the local image executor"
            )

    host = PrivateCompanionPlugin()
    result = await host._image_companion_generate(
        workflow_kind="selfie", prompt_text="test"
    )
    assert result[0] == "独立生图服务"
    assert "astrbot_plugin_image_companion" in result[2]


def test_image_companion_status_is_unavailable_without_external_plugin() -> None:
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: None

    status = harness._image_companion_status()

    assert status["installed"] is False
    assert status["available"] is False
    assert harness._image_companion_available() is False
    assert harness._image_companion_backend_available("external") is False


@pytest.mark.asyncio
async def test_endpoint_test_refreshes_stale_extension_api() -> None:
    calls = 0

    class CurrentApi:
        async def test_endpoint(self, owner, endpoint, prompt):
            return {"ok": True, "image_path": "result.png", "message": prompt}

    stale_api = SimpleNamespace()
    current_api = CurrentApi()
    harness = _BridgeHarness()

    def resolve_api():
        nonlocal calls
        calls += 1
        return stale_api if calls == 1 else current_api

    harness._image_companion_api = resolve_api
    result = await harness._image_companion_test_endpoint(
        {"base_url": "https://example.test/v1"},
        "endpoint probe",
    )

    assert calls == 2
    assert result == {
        "ok": True,
        "image_path": "result.png",
        "message": "endpoint probe",
    }


@pytest.mark.asyncio
async def test_endpoint_test_uses_refreshed_missing_result_for_diagnosis() -> None:
    stale_api = SimpleNamespace()
    harness = _BridgeHarness()
    resolved = iter((stale_api, None))
    harness._image_companion_api = lambda: next(resolved)

    result = await harness._image_companion_test_endpoint({}, "endpoint probe")

    assert result == {
        "ok": False,
        "message": "请安装并启用“我会画给你看”后再测试在线图片 API。",
    }


@pytest.mark.asyncio
async def test_image_companion_status_and_maintenance_delegate_to_external_api() -> (
    None
):
    calls: list[object] = []

    class Api:
        def capability_status(self, owner):
            calls.append(owner)
            return {
                "installed": True,
                "enabled": True,
                "available": True,
                "backends": {"external": True},
            }

        async def maintenance(self, owner):
            calls.append(("maintenance", owner))
            return {"removed_files": 2}

    harness = _BridgeHarness()
    harness._image_companion_api = lambda: Api()

    assert harness._image_companion_available() is True
    assert harness._image_companion_backend_available("external") is True
    assert await harness._image_companion_maintenance() == {"removed_files": 2}
    assert calls[-1] == ("maintenance", harness)
