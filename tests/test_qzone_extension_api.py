from __future__ import annotations

import asyncio
import json
import re
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from astrbot_plugin_private_companion import extension_api_qzone as qzone_api
from astrbot_plugin_private_companion.extension_api_qzone import (
    _QzoneCapabilityFamily,
)
from astrbot_plugin_private_companion.qzone_contract import (
    QZONE_API_FAMILY,
    QZONE_API_VERSION,
    QZONE_CAPABILITIES,
    QZONE_CONFIG_SNAPSHOT_FIELDS,
    QZONE_DESCRIPTOR_FIELDS,
    QZONE_OPERATION_VERSION,
    QZONE_PLUGIN_ID,
    QZONE_RESULT_FIELDS,
    QZONE_RESULT_VERSION,
    QZONE_TARGET_PLUGIN_ID,
    QzoneContractError,
    bounded_number,
    build_qzone_result,
    validate_qzone_config_snapshot,
    validate_qzone_operation_payload,
)


GENERATION = "a" * 32
OTHER_GENERATION = "b" * 32
RAW_COOKIE = "uin=o12345678; p_skey=do-not-export"


def _post() -> SimpleNamespace:
    return SimpleNamespace(
        tid="private-tid-42",
        uin="12345678",
        name="朋友\x00",
        text="今天下雨了\n带伞",
        create_time=1_700_000_000,
        image_items=[
            {"url": "https://cdn.example.test/photo.jpg?size=large"},
            {"url": "https://cdn.example.test/private.jpg?token=secret"},
            {"url": "file:///data/private/photo.jpg"},
        ],
        comments=[
            SimpleNamespace(
                uin="87654321",
                name="另一位朋友",
                content="记得保暖",
                create_time=1_700_000_001,
            )
        ],
        liked=False,
        raw={
            "cookie": RAW_COOKIE,
            "path": "/data/private/photo.jpg",
            "unikey": "raw-private-key",
        },
    )


class _Plugin:
    def __init__(self) -> None:
        self.context = object()
        self.data = {"qzone_integration": {}}
        self.enable_qzone_integration = True
        self.enable_qzone_life_publish = True
        self.enable_qzone_comment_inbox = True
        self.enable_qzone_generated_image_publish = False
        self.qzone_cookie = RAW_COOKIE
        self._qzone_last_bot = None
        self.posts = [_post()]
        self.calls: dict[str, int] = {
            "cookies": 0,
            "feed": 0,
            "publish": 0,
            "like": 0,
            "comment": 0,
            "delete": 0,
        }

    @staticmethod
    def _qzone_platform_supported(_event: Any = None) -> bool:
        return True

    @staticmethod
    def _qzone_runtime_bot_usable(_candidate: Any) -> bool:
        return False

    @staticmethod
    def _qzone_context_from_cookies(_cookies: str) -> dict[str, Any]:
        return {
            "uin": 12345678,
            "skey": "secret-skey",
            "p_skey": "secret-p-skey",
        }

    async def _qzone_get_cookies(self, _event: Any = None) -> str:
        self.calls["cookies"] += 1
        return RAW_COOKIE

    async def _qzone_query_feeds(self, _event: Any = None, **_kwargs: Any) -> list[Any]:
        self.calls["feed"] += 1
        return self.posts

    async def _publish_qzone_text(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls["publish"] += 1
        return {
            "success": True,
            "tid": "published-private-tid",
            "uin": "12345678",
            "text": "新动态",
            "images": ["/data/private/generated.png"],
            "image_count": 1,
            "verified": True,
        }

    async def _qzone_like_post(self, _event: Any, _post_value: Any) -> dict[str, Any]:
        self.calls["like"] += 1
        return {"success": True, "liked": True, "verified": True}

    async def _qzone_comment_post(
        self,
        _event: Any,
        _post_value: Any,
        content: str = "",
    ) -> str:
        self.calls["comment"] += 1
        return content

    async def _qzone_delete_post(self, _event: Any, _post_value: Any) -> None:
        self.calls["delete"] += 1


class _Owner:
    def __init__(
        self,
        plugin: _Plugin,
        *,
        generation: str = GENERATION,
        state: str = "ready",
    ) -> None:
        self._plugin = plugin
        self.generation = generation
        self.state = state
        self._qzone_reference_lock = threading.RLock()
        self._qzone_references: dict[str, tuple[float, Any]] = {}

    def _story_migration_instance_generation(self) -> str:
        return self.generation

    def _story_migration_lifecycle_state(self) -> str:
        return self.state


def _family(
    plugin: _Plugin | None = None,
    *,
    generation: str = GENERATION,
    state: str = "ready",
) -> tuple[_QzoneCapabilityFamily, _Owner, _Plugin]:
    host = plugin or _Plugin()
    owner = _Owner(host, generation=generation, state=state)
    return _QzoneCapabilityFamily(owner), owner, host


def _run(family: _QzoneCapabilityFamily, operation: str, payload: dict[str, Any]):
    return asyncio.run(family.execute_qzone_operation(operation, payload))


def test_descriptor_and_config_snapshot_are_exact_and_secret_free() -> None:
    family, _owner, _plugin = _family()

    descriptor = family.qzone_capabilities()
    assert set(descriptor) == QZONE_DESCRIPTOR_FIELDS
    assert descriptor == {
        "plugin_id": QZONE_PLUGIN_ID,
        "instance_generation": GENERATION,
        "api_family": QZONE_API_FAMILY,
        "api_version": QZONE_API_VERSION,
        "supported_task_versions": [QZONE_OPERATION_VERSION],
        "capabilities": list(QZONE_CAPABILITIES),
        "lifecycle_state": "ready",
        "degraded_reasons": [],
    }

    snapshot = family.export_qzone_config_snapshot(
        target_plugin_id=QZONE_TARGET_PLUGIN_ID,
    )
    assert set(snapshot) == QZONE_CONFIG_SNAPSHOT_FIELDS
    assert validate_qzone_config_snapshot(snapshot) == snapshot
    assert snapshot["credential"] == {
        "configured": True,
        "source": "manual",
        "state": "ready",
    }
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert RAW_COOKIE not in serialized
    assert "do-not-export" not in serialized
    assert "12345678" not in serialized
    assert "/data/" not in serialized

    with pytest.raises(QzoneContractError) as captured:
        family.export_qzone_config_snapshot(target_plugin_id="wrong-plugin")
    assert captured.value.code == "qzone_snapshot_target_unsupported"


def test_operation_payloads_are_exact_and_numeric_projection_is_bounded() -> None:
    assert validate_qzone_operation_payload("status", {}) == ("status", {})
    assert validate_qzone_operation_payload(
        "feed",
        {"scope": "profile", "target_uin": "12345678", "page": 10},
    ) == (
        "feed",
        {"scope": "profile", "target_uin": "12345678", "page": 10},
    )
    for operation, payload in (
        ("status", {"extra": True}),
        ("feed", {"scope": "self", "target_uin": "123", "page": 1}),
        ("feed", {"scope": "profile", "target_uin": "12", "page": 1}),
        ("publish", {"content": "x\n", "auto_generate_image": False}),
        ("comment", {"post_ref": "raw:tid", "content": "ok"}),
    ):
        with pytest.raises(QzoneContractError):
            validate_qzone_operation_payload(operation, payload)
    assert bounded_number(10**10_000, 0, 9) == 9
    assert bounded_number(float("inf"), 0, 9) == 0


def test_status_feed_and_detail_return_exact_path_free_results() -> None:
    family, _owner, plugin = _family()

    status = _run(family, "status", {})
    assert set(status) == QZONE_RESULT_FIELDS
    assert status["version"] == QZONE_RESULT_VERSION
    assert status["instance_generation"] == GENERATION
    assert status["operation"] == "status"
    assert status["ok"] is True
    assert set(status["data"]) == {
        "enabled",
        "available",
        "platform_supported",
        "service_available",
        "credential_state",
        "credential_source",
        "bound",
        "uin_masked",
        "features",
        "degraded_reasons",
    }

    feed = _run(
        family,
        "feed",
        {"scope": "self", "target_uin": "", "page": 1},
    )
    assert feed["ok"] is True
    item = feed["data"]["items"][0]
    assert re.fullmatch(r"qzref_[A-Za-z0-9_-]{32}", item["post_ref"])
    assert item["images"] == ["https://cdn.example.test/photo.jpg?size=large"]
    assert item["author"]["uin_masked"] == "12****78"
    assert "comments" not in item

    detail = _run(family, "detail", {"post_ref": item["post_ref"]})
    assert detail["ok"] is True
    assert detail["data"]["post"]["post_ref"] == item["post_ref"]
    assert detail["data"]["post"]["comments"][0]["content"] == "记得保暖"
    assert plugin.calls["feed"] == 2

    serialized = json.dumps(
        {"status": status, "feed": feed, "detail": detail},
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        RAW_COOKIE,
        "do-not-export",
        "secret-skey",
        "private-tid-42",
        "raw-private-key",
        "/data/private",
        "12345678",
    ):
        assert forbidden not in serialized


def test_mutations_use_opaque_refs_and_deleted_refs_are_not_reused() -> None:
    family, _owner, plugin = _family()
    feed = _run(
        family,
        "feed",
        {"scope": "friends", "target_uin": "", "page": 1},
    )
    reference = feed["data"]["items"][0]["post_ref"]

    liked = _run(family, "like", {"post_ref": reference})
    assert liked["ok"] is True
    assert liked["data"] == {
        "post_ref": reference,
        "liked": True,
        "verified": True,
        "verify_message": "verified",
    }
    commented = _run(
        family,
        "comment",
        {"post_ref": reference, "content": "注意安全"},
    )
    assert commented["ok"] is True
    assert commented["data"]["post"]["comments"][-1]["content"] == "注意安全"
    deleted = _run(family, "delete", {"post_ref": reference})
    assert deleted["ok"] is True
    assert deleted["data"] == {"post_ref": reference, "deleted": True}

    stale = _run(family, "like", {"post_ref": reference})
    assert stale["ok"] is False
    assert stale["code"] == "qzone_post_ref_stale"
    assert plugin.calls["like"] == 1
    assert plugin.calls["comment"] == 1
    assert plugin.calls["delete"] == 1

    published = _run(
        family,
        "publish",
        {"content": "新动态", "auto_generate_image": False},
    )
    assert published["ok"] is True
    assert set(published["data"]) == {
        "post_ref",
        "text",
        "image_count",
        "verified",
        "verify_message",
    }
    serialized = json.dumps(published, ensure_ascii=False, sort_keys=True)
    assert "published-private-tid" not in serialized
    assert "/data/private" not in serialized


def test_generation_swap_invalidates_old_family_and_old_post_refs() -> None:
    plugin = _Plugin()
    old_family, old_owner, _plugin = _family(plugin)
    feed = _run(
        old_family,
        "feed",
        {"scope": "friends", "target_uin": "", "page": 1},
    )
    reference = feed["data"]["items"][0]["post_ref"]
    old_owner.state = "superseded"

    old_result = _run(old_family, "detail", {"post_ref": reference})
    assert old_result["ok"] is False
    assert old_result["code"] == "qzone_generation_stale"

    new_family, _new_owner, _plugin = _family(
        plugin,
        generation=OTHER_GENERATION,
    )
    new_result = _run(new_family, "detail", {"post_ref": reference})
    assert new_result["ok"] is False
    assert new_result["code"] == "qzone_post_ref_stale"
    assert new_result["instance_generation"] == OTHER_GENERATION


def test_cancellation_propagates_and_unexpected_errors_are_body_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family, _owner, plugin = _family()

    async def cancel_query(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise asyncio.CancelledError

    plugin._qzone_query_feeds = cancel_query
    with pytest.raises(asyncio.CancelledError):
        _run(
            family,
            "feed",
            {"scope": "friends", "target_uin": "", "page": 1},
        )

    warnings: list[tuple[Any, ...]] = []

    async def fail_query(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise RuntimeError("cookie=raw-secret /data/private")

    plugin._qzone_query_feeds = fail_query
    monkeypatch.setattr(
        qzone_api,
        "logger",
        SimpleNamespace(warning=lambda *args, **_kwargs: warnings.append(args)),
    )
    failure = _run(
        family,
        "feed",
        {"scope": "friends", "target_uin": "", "page": 1},
    )
    assert failure["ok"] is False
    assert failure["code"] == "qzone_operation_failed"
    assert "raw-secret" not in json.dumps(failure, ensure_ascii=False)
    assert "raw-secret" not in repr(warnings)
    assert "RuntimeError" in repr(warnings)


def test_result_size_limit_fails_closed() -> None:
    with pytest.raises(QzoneContractError) as captured:
        build_qzone_result(
            instance_generation=GENERATION,
            operation="status",
            ok=True,
            code="",
            message="",
            data={"value": "x" * (17 * 1024)},
        )
    assert captured.value.code == "qzone_result_too_large"
