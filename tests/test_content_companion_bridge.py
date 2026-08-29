# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from astrbot_plugin_private_companion.content_companion_bridge import ContentCompanionBridgeMixin
from astrbot_plugin_private_companion.story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
    story_authority_controller,
)
from story_migration_contract import (
    STORY_MIGRATION_OWNER_ID,
    build_story_migration_snapshot,
)


class _LegacyCreative:
    async def _maybe_start_creative_project(self, *, idle_checked=False):
        return "legacy"


class _FallbackCreative(ContentCompanionBridgeMixin, _LegacyCreative):
    context = None


def test_story_handoff_fresh_resolve_invalidates_positive_cache() -> None:
    stale = object()
    current = object()
    host = _FallbackCreative()
    host._external_bridge_resolver_cache = {
        "content_companion": {
            "api": stale,
            "expires_at": time.monotonic() + 15.0,
        }
    }
    module_name = "astrbot_plugin_content_companion.main"
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = SimpleNamespace(
        PLUGIN_NAME="astrbot_plugin_content_companion",
        get_content_companion_api=lambda: current,
    )
    try:
        assert host._content_companion_api_fresh() is current
        assert host._external_bridge_resolver_cache["content_companion"]["api"] is current
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


@pytest.fixture
def open_story_authority():
    controller = story_authority_controller()
    generation = uuid.uuid4().hex
    controller.stage_generation(generation)
    controller.activate_generation(generation)
    try:
        yield controller, generation
    finally:
        controller.activate_generation(f"test-open-{uuid.uuid4().hex}")


@pytest.mark.asyncio
async def test_content_bridge_falls_back_when_extension_is_missing(
    open_story_authority,
) -> None:
    host = _FallbackCreative()
    assert await host._maybe_start_creative_project(idle_checked=True) is True
    assert host._content_companion_status()["installed"] is False


@pytest.mark.asyncio
async def test_content_bridge_delegates_to_loaded_extension(
    open_story_authority,
) -> None:
    calls = []

    class Api:
        def status(self):
            return {"installed": True, "enabled": True, "available": True}

        async def maybe_start_creative_project(self, owner, *, idle_checked=False):
            calls.append((owner, idle_checked))
            return True

    module_name = "astrbot_plugin_content_companion.main"
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = SimpleNamespace(get_content_companion_api=lambda: Api())
    try:
        host = _FallbackCreative()
        assert await host._maybe_start_creative_project(idle_checked=True) is True
        assert calls == [(host, True)]
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


@pytest.mark.asyncio
async def test_content_bridge_has_zero_delegate_and_fallback_calls_while_draining_or_leased(
    open_story_authority,
) -> None:
    controller, generation = open_story_authority

    class LegacyCreative:
        async def _maybe_advance_creative_projects(self):
            self.super_calls.append("advance")

        async def _maybe_start_creative_project(self, **_kwargs):
            self.super_calls.append("start")
            return True

        async def _generate_creative_project(self, *_args):
            self.super_calls.append("project")
            return {}

        async def _generate_creative_chunk(self, *_args):
            self.super_calls.append("chunk")
            return ""

        async def _review_creative_chunk(self, *_args, **_kwargs):
            self.super_calls.append("review")
            return {}

        async def _apply_creative_manual_edit(self, *_args, **_kwargs):
            self.super_calls.append("edit")
            return {}

        async def _rebuild_creative_memory_from_project(self, *_args):
            self.super_calls.append("rebuild")
            return {}

        async def _maybe_generate_creative_cover(self, *_args, **_kwargs):
            self.super_calls.append("cover")
            return {}

    class Host(ContentCompanionBridgeMixin, LegacyCreative):
        context = None

        def __init__(self):
            self.delegate_calls = []
            self.super_calls = []

        def _content_companion_available(self):
            return True

        async def _content_companion_call(self, operation, *_args, **_kwargs):
            self.delegate_calls.append(operation)
            return {}

    host = Host()
    calls = (
        ("_maybe_advance_creative_projects", (), {}),
        ("_maybe_start_creative_project", (), {"idle_checked": True}),
        ("_generate_creative_project", ({"text": "seed"},), {}),
        ("_generate_creative_chunk", ({"id": "work"}, 100), {}),
        ("_review_creative_chunk", ({},), {}),
        ("_apply_creative_manual_edit", ("work",), {}),
        ("_rebuild_creative_memory_from_project", ("work",), {}),
        ("_maybe_generate_creative_cover", ("work",), {"force": True}),
    )

    entered = asyncio.Event()
    release = asyncio.Event()

    async def existing_root() -> None:
        identity = controller.enter_legacy_operation("existing")
        try:
            entered.set()
            await release.wait()
        finally:
            controller.exit_legacy_operation(identity)

    async def snapshot_factory() -> dict:
        return build_story_migration_snapshot([])

    root_task = asyncio.create_task(existing_root())
    await entered.wait()
    preparing = asyncio.create_task(
        controller.prepare(
            generation=generation,
            target_plugin_id=STORY_HANDOFF_TARGET_PLUGIN_ID,
            owner_id=STORY_MIGRATION_OWNER_ID,
            snapshot_factory=snapshot_factory,
        )
    )
    while controller.debug_state()["state"] != "draining":
        await asyncio.sleep(0)
    for method_name, args, kwargs in calls:
        with pytest.raises(StoryAuthorityError) as rejected:
            await getattr(host, method_name)(*args, **kwargs)
        assert rejected.value.code == "story_legacy_write_draining"
    assert host.delegate_calls == host.super_calls == []

    release.set()
    lease = await preparing
    await root_task
    for method_name, args, kwargs in calls:
        with pytest.raises(StoryAuthorityError) as rejected:
            await getattr(host, method_name)(*args, **kwargs)
        assert rejected.value.code == "story_legacy_write_leased"
    assert host.delegate_calls == host.super_calls == []
    controller.abort(
        generation=generation,
        lease_token=lease["lease_token"],
    )
