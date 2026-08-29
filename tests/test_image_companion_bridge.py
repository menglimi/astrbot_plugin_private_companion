from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ImageGenerationRuntime = pytest.importorskip(
    "astrbot_plugin_image_companion.image_runtime"
).ImageGenerationRuntime
ImagePhotoReference = pytest.importorskip(
    "astrbot_plugin_image_companion.photo_reference_catalog"
).PhotoReference
from astrbot_plugin_private_companion.image_companion_bridge import (
    ImageCompanionBridgeMixin,
)
from astrbot_plugin_private_companion.photo_reference_catalog import (
    load_catalog as load_private_catalog,
)


class _BridgeHarness(ImageCompanionBridgeMixin):
    context = None


_FORMAL_CAPABILITIES = [
    "image.build-task",
    "image.validate-task",
    "image.import-references",
    "image.release-reference-import",
    "image.execute-task",
    "image.execute-task.active",
]


class _CurrentImageApi:
    def __init__(self, output_path: Path, *, generation: int = 7) -> None:
        self.generation = generation
        self.output_path = output_path
        self.import_requests: list[dict[str, object]] = []
        self.builder_inputs: list[dict[str, object]] = []
        self.validated_tasks: list[dict[str, object]] = []
        self.executed_tasks: list[dict[str, object]] = []
        self.released: list[str] = []

    def capabilities(self) -> dict[str, object]:
        return {
            "plugin_id": "astrbot_plugin_image_companion",
            "instance_generation": self.generation,
            "api_family": "image.generation",
            "api_version": "image.generation-api.v1",
            "supported_task_versions": ["image.task.v1"],
            "capabilities": list(_FORMAL_CAPABILITIES),
            "lifecycle_state": "ready",
            "degraded_reasons": [],
        }

    def versions(self) -> dict[str, object]:
        return {
            "plugin_id": "astrbot_plugin_image_companion",
            "instance_generation": self.generation,
            "api_family": "image.generation",
            "api_version": "image.generation-api.v1",
            "task_version": "image.task.v1",
            "supported_task_versions": ["image.task.v1"],
        }

    def status(self) -> dict[str, object]:
        return {
            "installed": True,
            "enabled": True,
            "available": True,
            "backends": {"external": True},
        }

    async def import_references(self, value: object) -> dict[str, object]:
        assert type(value) is dict
        self.import_requests.append(value)
        return {
            "result_version": "image.reference-import-result.v1",
            "status": "succeeded",
            "instance_generation": self.generation,
            "lease_id": "reflease_" + "a" * 48,
            "asset_ids": [
                "ref_" + f"{index + 1:048x}"
                for index, _item in enumerate(value["assets"])
            ],
            "ttl_seconds": 90,
            "error": None,
        }

    def release_reference_import(self, lease_id: object) -> bool:
        assert type(lease_id) is str
        self.released.append(lease_id)
        return True

    def build_task(self, value: object) -> dict[str, object]:
        assert type(value) is dict
        self.builder_inputs.append(value)
        return {"canonical": dict(value)}

    def validate_task(self, value: object) -> dict[str, object]:
        assert type(value) is dict
        self.validated_tasks.append(value)
        canonical = value["canonical"]
        return {
            "valid": True,
            "task_version": "image.task.v1",
            "operation": "generate",
            "workflow_kind": canonical["workflow_kind"],
        }

    async def execute_task(self, value: object) -> dict[str, object]:
        assert type(value) is dict
        self.executed_tasks.append(value)
        return {
            "result_version": "image.result.v1",
            "task_version": "image.task.v1",
            "request_id": "b" * 32,
            "status": "succeeded",
            "backend": "external",
            "backend_task_id": "task-1",
            "output": {
                "asset_id": "image_" + "c" * 32,
                "kind": "image",
                "media_type": "image/png",
                "local_path": str(self.output_path.resolve()),
                "sha256": "d" * 64,
                "size_bytes": 16,
            },
            "error": None,
            "degraded_capabilities": [],
        }


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
    assert Path(runtime.data_dir) == image_data_dir
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
async def test_split_runtime_never_writes_remote_catalog_back_to_owner(
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

    assert received == []
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
async def test_current_image_contract_uses_owner_free_task_and_transient_bytes(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    output = tmp_path / "output.png"
    api = _CurrentImageApi(output)
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="take a quiet portrait",
        request_text="take a quiet portrait",
        session_key="private:user-a",
        requester_user_id="user-a",
        requester_is_private=True,
        reference_image_path=str(reference.resolve()),
        image_size="1024x1024",
    )

    assert result == (
        "external",
        str(output.resolve()),
        "已通过 ImageTask v1 生成受管图片。",
    )
    assert len(api.import_requests) == 1
    imported = api.import_requests[0]
    assert imported == {
        "version": "image.reference-import.v1",
        "owner_id": "astrbot_plugin_private_companion",
        "requester_id": "user-a",
        "scope": "private",
        "privacy": "private",
        "session_id": "private:user-a",
        "assets": [{"content": reference.read_bytes()}],
    }
    assert len(api.builder_inputs) == 1
    built = api.builder_inputs[0]
    assert built["owner_id"] == "astrbot_plugin_private_companion"
    assert built["reference_asset_ids"] == ["ref_" + f"{1:048x}"]
    assert built["reference_asset_roles"] == [["identity"]]
    assert built["limits"] == {"image_size": "1024x1024"}
    assert "reference_image_path" not in built
    assert "reference_image_paths" not in built
    assert all(value is not harness for value in built.values())
    assert len(api.validated_tasks) == len(api.executed_tasks) == 1
    assert api.released == ["reflease_" + "a" * 48]
    assert harness._image_companion_last_metadata() == {
        "trace": "b" * 32,
        "managed_asset_id": "image_" + "c" * 32,
        "reference_used": True,
        "reference_roles": ["identity"],
        "output_sha256": "d" * 64,
    }


@pytest.mark.asyncio
async def test_formal_image_legacy_rollout_uses_explicit_compatibility_method(
    tmp_path: Path,
) -> None:
    compatibility_calls: list[tuple[object, dict[str, object]]] = []

    class CompatibilityApi(_CurrentImageApi):
        def capabilities(self) -> dict[str, object]:
            value = super().capabilities()
            value["capabilities"].remove("image.execute-task.active")
            return value

        async def generate_for_companion(self, owner, request):
            compatibility_calls.append((owner, request))
            return {
                "handled": True,
                "backend": "legacy-rollout",
                "image_path": str((tmp_path / "legacy.png").resolve()),
                "note": "compatibility",
            }

    api = CompatibilityApi(tmp_path / "unused.png")
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="test",
    )

    assert result == (
        "legacy-rollout",
        str((tmp_path / "legacy.png").resolve()),
        "compatibility",
    )
    assert compatibility_calls == [
        (
            harness,
            {
                "workflow_kind": "selfie",
                "prompt_text": "test",
                "session_key": "test",
            },
        )
    ]
    assert api.import_requests == []
    assert api.builder_inputs == []
    assert api.executed_tasks == []


@pytest.mark.asyncio
async def test_formal_image_compatibility_hot_swap_rejects_old_generation(
    tmp_path: Path,
) -> None:
    current: list[object] = []
    replacement = _CurrentImageApi(tmp_path / "replacement.png", generation=8)

    class CompatibilityApi(_CurrentImageApi):
        def capabilities(self) -> dict[str, object]:
            value = super().capabilities()
            value["capabilities"].remove("image.execute-task.active")
            return value

        async def generate_for_companion(self, _owner, _request):
            current[0] = replacement
            return {
                "handled": True,
                "backend": "old-generation",
                "image_path": str((tmp_path / "old.png").resolve()),
                "note": "must not be accepted",
            }

    original = CompatibilityApi(tmp_path / "unused.png", generation=7)
    current.append(original)
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: current[0]

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="test",
    )

    assert result[1] == ""
    assert "拒绝接受旧代结果" in result[2]
    assert replacement.executed_tasks == []


@pytest.mark.asyncio
async def test_partial_formal_image_api_never_downgrades_to_owner_injection() -> None:
    legacy_calls = 0

    class PartialApi:
        build_task = object()

        async def generate_for_companion(self, _owner, _request):
            nonlocal legacy_calls
            legacy_calls += 1
            return {"handled": True}

    api = PartialApi()
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="test",
    )

    assert result[1] == ""
    assert "拒绝降级" in result[2]
    assert legacy_calls == 0


@pytest.mark.asyncio
async def test_unknown_formal_image_version_never_calls_legacy_method(
    tmp_path: Path,
) -> None:
    legacy_calls = 0

    class FutureApi(_CurrentImageApi):
        def capabilities(self) -> dict[str, object]:
            value = super().capabilities()
            value["api_version"] = "image.generation-api.v999"
            return value

        async def generate_for_companion(self, _owner, _request):
            nonlocal legacy_calls
            legacy_calls += 1
            return {"handled": True}

    api = FutureApi(tmp_path / "output.png")
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="test",
    )

    assert result[1] == ""
    assert "descriptor_incompatible" in result[2]
    assert legacy_calls == 0


@pytest.mark.asyncio
async def test_current_image_hot_swap_after_execute_fails_closed_and_releases(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nhot-swap")
    current: list[object] = []
    replacement = _CurrentImageApi(tmp_path / "replacement.png", generation=8)

    class SwappingApi(_CurrentImageApi):
        async def execute_task(self, value: object) -> dict[str, object]:
            result = await super().execute_task(value)
            current[0] = replacement
            return result

    original = SwappingApi(tmp_path / "old-output.png", generation=7)
    current.append(original)
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: current[0]

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="private:user-a",
        requester_user_id="user-a",
        requester_is_private=True,
        reference_image_path=str(reference.resolve()),
    )

    assert result[1] == ""
    assert "descriptor_incompatible" in result[2]
    assert original.released == ["reflease_" + "a" * 48]
    assert replacement.executed_tasks == []


@pytest.mark.asyncio
async def test_current_image_cancellation_propagates_and_releases_import(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\ncancel")
    started = asyncio.Event()
    blocker = asyncio.Event()

    class BlockingApi(_CurrentImageApi):
        async def execute_task(self, value: object) -> dict[str, object]:
            assert type(value) is dict
            started.set()
            await blocker.wait()
            raise AssertionError("cancelled execution must not resume")

    api = BlockingApi(tmp_path / "output.png")
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api
    pending = asyncio.create_task(
        harness._image_companion_generate(
            workflow_kind="selfie",
            prompt_text="test",
            session_key="private:user-a",
            requester_user_id="user-a",
            requester_is_private=True,
            reference_image_path=str(reference.resolve()),
        )
    )
    await started.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert api.released == ["reflease_" + "a" * 48]


@pytest.mark.asyncio
async def test_current_image_rejects_fifo_reference_without_import(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("non-blocking FIFO inspection is unavailable")
    fifo = tmp_path / "reference.png"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("FIFOs are unavailable")
    api = _CurrentImageApi(tmp_path / "output.png")
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    result = await asyncio.wait_for(
        harness._image_companion_generate(
            workflow_kind="selfie",
            prompt_text="test",
            session_key="test",
            reference_image_path=str(fifo.resolve()),
        ),
        timeout=1.0,
    )

    assert result[1] == ""
    assert "reference_asset_not_regular" in result[2]
    assert api.import_requests == []
    assert api.executed_tasks == []


def test_current_image_status_uses_owner_free_surface(tmp_path: Path) -> None:
    class Api(_CurrentImageApi):
        def capability_status(self, _owner):
            raise AssertionError("formal status must not receive Companion owner")

        def local_load_state(self, _owner, **_kwargs):
            raise AssertionError("formal load status must not receive Companion owner")

    api = Api(tmp_path / "output.png")
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: api

    assert harness._image_companion_available() is True
    assert harness._image_companion_backend_available("external") is True
    assert harness._image_companion_load_state()["available"] is True
    assert asyncio.run(harness._image_companion_maintenance()) == {}


def test_current_image_status_fails_closed_on_hot_swap(tmp_path: Path) -> None:
    current: list[object] = []
    replacement = _CurrentImageApi(tmp_path / "replacement.png", generation=8)

    class SwappingApi(_CurrentImageApi):
        def status(self) -> dict[str, object]:
            current[0] = replacement
            return super().status()

    current.append(SwappingApi(tmp_path / "old.png", generation=7))
    harness = _BridgeHarness()
    harness._image_companion_api = lambda: current[0]

    status = harness._image_companion_status()

    assert status["available"] is False
    assert status["reason"] == "descriptor_incompatible"


def test_current_image_status_query_failure_is_unavailable(tmp_path: Path) -> None:
    class BrokenApi(_CurrentImageApi):
        def status(self) -> dict[str, object]:
            raise RuntimeError("must not be exposed")

    harness = _BridgeHarness()
    api = BrokenApi(tmp_path / "output.png")
    harness._image_companion_api = lambda: api

    status = harness._image_companion_status()

    assert status["available"] is False
    assert status["reason"] == "image_status_query_failed"


@pytest.mark.asyncio
async def test_current_image_rejects_control_characters_in_failure_code(
    tmp_path: Path,
) -> None:
    class BrokenApi(_CurrentImageApi):
        async def execute_task(self, value: object) -> dict[str, object]:
            result = await super().execute_task(value)
            result["status"] = "failed"
            result["output"] = None
            result["error"] = {"code": "backend_failed\nsecret", "stage": "backend"}
            return result

    harness = _BridgeHarness()
    api = BrokenApi(tmp_path / "output.png")
    harness._image_companion_api = lambda: api

    result = await harness._image_companion_generate(
        workflow_kind="selfie",
        prompt_text="test",
        session_key="test",
    )

    assert result[1] == ""
    assert "image_result_malformed" in result[2]
    assert "secret" not in result[2]


@pytest.mark.asyncio
async def test_descriptorless_image_api_never_receives_companion_owner() -> None:
    calls = 0

    class Api:
        async def generate_for_companion(self, _owner, _request):
            nonlocal calls
            calls += 1
            return {"handled": True}

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

    assert result[1] == ""
    assert "descriptor_method_missing" in result[2]
    assert calls == 0


@pytest.mark.asyncio
async def test_split_runtime_does_not_call_owner_legacy_executor(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    materialized: list[str] = []

    class Service:
        data_dir = "C:/image-companion"
        image_data_lock = __import__("asyncio").Lock()

        @staticmethod
        def image_setting(_name, default):
            return default

        @staticmethod
        def image_data_for(_owner):
            return {}

        @staticmethod
        async def materialize_legacy_output(source: str) -> str:
            materialized.append(source)
            return source

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
    assert materialized == ["C:/output.png"]


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
async def test_descriptorless_status_and_maintenance_never_receive_owner() -> (
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

    assert harness._image_companion_available() is False
    assert harness._image_companion_backend_available("external") is False
    assert await harness._image_companion_maintenance() == {}
    assert calls == []
