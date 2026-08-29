# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import copy
import hashlib
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion import content_companion_bridge as bridge_module
from astrbot_plugin_private_companion import story_authority as authority_module
from astrbot_plugin_private_companion import story_handoff as handoff_module
from astrbot_plugin_private_companion.content_companion_bridge import (
    _CONTENT_API_FAMILY,
    _CONTENT_API_VERSION,
    _CONTENT_MODEL_EXECUTION_TOKEN_LIMIT,
    _CONTENT_REQUIRED_CAPABILITIES,
    _CONTENT_SERVICES_VERSION,
    _CONTENT_STORY_OWNER_ID,
    _CONTENT_TASK_VERSION,
    _ContentStoryModelBudget,
    ContentCompanionBridgeMixin,
)
from astrbot_plugin_private_companion.external_bridge_resolver import (
    invalidate_external_bridge_cache,
)
from astrbot_plugin_private_companion.token_budget import TokenBudgetMixin


TARGET_GENERATION = "7" * 32
NEXT_GENERATION = "8" * 32
SOURCE_GENERATION = "9" * 32
_MIGRATION_CAPABILITIES = {
    "story.migration.abort",
    "story.migration.commit",
    "story.migration.prepare",
    "story.migration.status",
}
_SUPPORTED_TASK_VERSIONS = [_CONTENT_TASK_VERSION, "content.story-task.v2"]
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _descriptor(
    *,
    generation: str = TARGET_GENERATION,
    state: str = "ready",
    enforced: bool = False,
) -> dict[str, Any]:
    capabilities = {*_CONTENT_REQUIRED_CAPABILITIES, *_MIGRATION_CAPABILITIES}
    if enforced:
        capabilities.add("story.handoff.enforced")
    return {
        "plugin_id": "astrbot_plugin_content_companion",
        "instance_generation": generation,
        "api_family": _CONTENT_API_FAMILY,
        "api_version": _CONTENT_API_VERSION,
        "supported_task_versions": list(_SUPPORTED_TASK_VERSIONS),
        "capabilities": sorted(capabilities),
        "lifecycle_state": state,
        "degraded_reasons": [] if state == "ready" else ["story_service_closed"],
    }


def _versions(*, generation: str = TARGET_GENERATION) -> dict[str, Any]:
    return {
        "plugin_id": "astrbot_plugin_content_companion",
        "instance_generation": generation,
        "api_family": _CONTENT_API_FAMILY,
        "api_version": _CONTENT_API_VERSION,
        "task_version": _CONTENT_TASK_VERSION,
        "supported_task_versions": list(_SUPPORTED_TASK_VERSIONS),
        "services_version": _CONTENT_SERVICES_VERSION,
    }


class _CurrentAPI:
    def __init__(
        self,
        *,
        generation: str = TARGET_GENERATION,
        state: str = "ready",
    ) -> None:
        self.generation = generation
        self.state = state
        self.enforced = False
        self.marker: dict[str, Any] | None = None
        self.tasks: list[dict[str, Any]] = []
        self.services: list[dict[str, Any]] = []
        self.execute_impl: Any = None

    def capabilities(self) -> dict[str, Any]:
        return _descriptor(
            generation=self.generation,
            state=self.state,
            enforced=self.enforced,
        )

    def versions(self) -> dict[str, Any]:
        return _versions(generation=self.generation)

    def build_task(self, value: Any) -> dict[str, Any]:
        task = dict(value)
        self.tasks.append(task)
        return task

    def validate_task(self, value: Any) -> dict[str, Any]:
        return {"valid": type(value) is dict}

    def story_migration_status(self) -> dict[str, Any]:
        if self.marker is None:
            return {
                "version": "content.story-migration-ledger.v1",
                "status": "absent",
                "target_plugin_id": "astrbot_plugin_content_companion",
                "owner_id": _CONTENT_STORY_OWNER_ID,
            }
        return {
            "version": "content.story-migration-ledger.v1",
            "status": "committed",
            "source_plugin_id": _CONTENT_STORY_OWNER_ID,
            "source_instance_generation": SOURCE_GENERATION,
            "marker": copy.deepcopy(self.marker),
            "backup": {
                "sha256": _EMPTY_SHA256,
                "size": 0,
                "existed": False,
            },
        }

    async def prepare_story_migration(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self.story_migration_status()

    async def commit_story_migration(self, marker: Any) -> dict[str, Any]:
        self.marker = copy.deepcopy(marker)
        self.enforced = True
        return self.story_migration_status()

    async def abort_story_migration(self, **_kwargs: Any) -> dict[str, Any]:
        return self.story_migration_status()

    async def execute_task(self, task: Any, services: Any) -> dict[str, Any]:
        self.services.append(dict(services))
        if self.execute_impl is not None:
            return await self.execute_impl(task, services)
        return {"project": {"id": "story-1"}}


@contextmanager
def _mounted(api_getter: Any):
    name = "astrbot_plugin_content_companion.main"
    previous = sys.modules.get(name)
    module = SimpleNamespace(get_content_companion_api=api_getter)
    sys.modules[name] = module
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class _LegacyCreative:
    def __init__(self) -> None:
        self.legacy_calls: list[str] = []

    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        self.legacy_calls.append("start")
        return True

    async def _generate_creative_project(self, _source: Any) -> dict[str, Any]:
        self.legacy_calls.append("generate_project")
        return {"id": "legacy"}

    async def _generate_creative_chunk(self, _project: Any, _budget: int) -> str:
        self.legacy_calls.append("generate_chunk")
        return "legacy"

    async def _review_creative_chunk(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.legacy_calls.append("review")
        return {"passed": True}

    async def _apply_creative_manual_edit(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.legacy_calls.append("manual")
        return {"success": True}

    async def _rebuild_creative_memory_from_project(self, _project_id: str) -> dict[str, Any]:
        self.legacy_calls.append("rebuild")
        return {"success": True}

    async def _maybe_advance_creative_projects(self) -> None:
        self.legacy_calls.append("advance")


class _Host(ContentCompanionBridgeMixin, _LegacyCreative):
    context = None

    def __init__(self) -> None:
        super().__init__()
        self._data_lock = asyncio.Lock()
        self._data_default: dict[str, Any] = {}
        self.data = self._data_default
        self._persisted_marker: dict[str, Any] | None = None
        self.creative_provider_id = "creative-provider"
        self.creative_outline_provider_id = "outline-provider"
        self.creative_review_provider_id = "review-provider"
        self.mai_style_provider_id = "style-provider"
        self.model_calls: list[dict[str, Any]] = []
        self.enable_creative_writing = True

    def _read_story_migration_commit_persisted_sync(self) -> tuple[bool, Any]:
        if self._persisted_marker is None:
            return False, None
        return True, copy.deepcopy(self._persisted_marker)

    @staticmethod
    def _task_provider(*values: Any, allow_replacement: bool = True) -> str:
        assert allow_replacement is False
        return next((str(value) for value in values if value), "")

    @staticmethod
    def _estimate_model_request_tokens(
        prompt: Any,
        *,
        max_tokens: Any = 0,
        **_kwargs: Any,
    ) -> int:
        return max(1, len(str(prompt)) // 4 + int(max_tokens))

    async def _llm_call(self, prompt: str, **kwargs: Any) -> str:
        self.model_calls.append({"prompt": prompt, **kwargs})
        return "accepted"

    @staticmethod
    def _bot_currently_idle_for_creative_writing() -> bool:
        return True

    @staticmethod
    def _creative_has_pending_proactive_plan() -> bool:
        return False

    @staticmethod
    def _creative_inspiration_source() -> dict[str, str]:
        return {"source": "test", "label": "test", "text": "seed"}


class _CallbackHost(_Host):
    def __init__(self) -> None:
        super().__init__()
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {
                "friend": {
                    "enabled": True,
                    "umo": "platform:friend",
                }
            }
        }
        self._data_default = self.data
        self.progress_calls: list[dict[str, Any]] = []
        self.share_calls: list[tuple[dict[str, Any], bool]] = []
        self.saved_sections: list[set[str]] = []
        self.schedule_result = True

    async def _memory_companion_record_creative_progress(self, **kwargs: Any) -> None:
        self.progress_calls.append(kwargs)
        kwargs["project"]["title"] = "callback-only"

    def _schedule_creative_share_candidate(
        self,
        candidate: dict[str, Any],
        *,
        mark_disclosed: bool = True,
    ) -> bool:
        self.share_calls.append((candidate, mark_disclosed))
        if not self.schedule_result:
            self.data["users"]["friend"]["transient"] = "rollback"
            return False
        self.data["users"]["friend"]["last_creative_share_key"] = candidate["key"]
        return True

    def _save_data_sync(self, *, sections: set[str]) -> None:
        self.saved_sections.append(set(sections))


def _progress_payload() -> dict[str, Any]:
    return {
        "event": "project-advanced",
        "project": {
            "id": "story-1",
            "owner_id": _CONTENT_STORY_OWNER_ID,
            "title": "A title",
            "work_type": "novel",
            "premise": "premise",
            "tone": "quiet",
            "status": "drafting",
            "current_chars": 240,
            "target_chars": 1000,
            "next_hint": "next",
        },
        "chunk": "bounded excerpt",
        "extract": {
            "next_direction": "next",
            "important_facts": ["fact"],
            "new_threads": ["thread"],
        },
    }


def _share_payload() -> dict[str, Any]:
    return {
        "key": "story-1:opening",
        "milestone": "opening",
        "disclosure_kind": "milestone",
        "project_id": "story-1",
        "work_type": "novel",
        "title": "A title",
        "premise": "premise",
        "tone": "quiet",
        "source": "seed",
        "snippet": "bounded excerpt",
        "current_chars": 240,
        "target_chars": 1000,
        "chunk_count": 2,
        "maturity_score": 44.5,
        "completion_ratio": 0.24,
        "status": "drafting",
        "created_ts": 1_800_000_000.0,
    }


@pytest.fixture
def story_controller(monkeypatch: pytest.MonkeyPatch):
    controller = authority_module._StoryAuthorityController()
    monkeypatch.setattr(
        bridge_module,
        "story_authority_controller",
        lambda: controller,
    )
    monkeypatch.setattr(
        handoff_module,
        "story_authority_controller",
        lambda: controller,
    )
    return controller


def _authorize_current_story(
    host: _Host,
    api: _CurrentAPI,
    controller: Any,
) -> dict[str, Any]:
    digest = hashlib.sha256(b"companion-content-s4").hexdigest()
    marker = {
        "version": handoff_module.STORY_MIGRATION_COMMIT_VERSION,
        "snapshot_id": f"storysnap_{digest}",
        "snapshot_sha256": digest,
        "target_plugin_id": "astrbot_plugin_content_companion",
        "owner_id": _CONTENT_STORY_OWNER_ID,
        "committed_at": 1.0,
    }
    host._data_default[handoff_module.STORY_MIGRATION_COMMIT_KEY] = copy.deepcopy(
        marker
    )
    host._persisted_marker = copy.deepcopy(marker)
    api.marker = copy.deepcopy(marker)
    api.enforced = True
    controller.recover_committed_marker(marker, source_verified=True)
    return marker


@pytest.mark.asyncio
async def test_current_contract_builds_exact_owner_scoped_task_and_budgeted_services(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()

    async def execute(_task: Any, services: Any) -> dict[str, Any]:
        result = await services["call_model"](
            provider_role="creative_project",
            prompt="bounded prompt",
            max_tokens=500,
        )
        return {"model": result}

    api.execute_impl = execute
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        claimed, result = await host._content_story_execute(
            "generate_project",
            author_prompt="seed",
        )
    invalidate_external_bridge_cache(host)

    assert claimed is True
    assert result == {"model": "accepted"}
    assert api.tasks == [
        {
            "version": _CONTENT_TASK_VERSION,
            "operation": "generate_project",
            "owner_id": _CONTENT_STORY_OWNER_ID,
            "max_model_calls": 1,
            "author_prompt": "seed",
        }
    ]
    assert set(api.services[0]) == {
        "version",
        "call_model",
        "record_progress",
        "offer_share",
    }
    assert api.services[0]["version"] == _CONTENT_SERVICES_VERSION
    assert callable(api.services[0]["record_progress"])
    assert callable(api.services[0]["offer_share"])
    assert host.model_calls[0]["provider_id"] == "creative-provider"
    assert host.model_calls[0]["strict_provider"] is True
    assert host.model_calls[0]["max_tokens"] == 500


@pytest.mark.asyncio
async def test_current_contract_stays_standby_before_durable_marker(
    story_controller: Any,
) -> None:
    del story_controller
    api = _CurrentAPI()
    host = _Host()
    with _mounted(lambda: api):
        result = await host._generate_creative_project({"text": "seed"})
    invalidate_external_bridge_cache(host)

    assert result == {"id": "legacy"}
    assert api.tasks == []
    assert api.services == []
    assert host.legacy_calls == ["generate_project"]


@pytest.mark.asyncio
async def test_committed_source_marker_cannot_disappear_from_live_primary_state(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    host._data_default.pop(handoff_module.STORY_MIGRATION_COMMIT_KEY)
    with _mounted(lambda: api):
        result = await host._generate_creative_project({"text": "seed"})
    invalidate_external_bridge_cache(host)

    assert result is None
    assert api.tasks == []
    assert host.legacy_calls == []
    assert story_controller.authority_state() == "blocked"


@pytest.mark.asyncio
async def test_post_await_content_generation_swap_fails_closed_without_local_retry(
    story_controller: Any,
) -> None:
    first = _CurrentAPI()
    replacement = _CurrentAPI(generation=NEXT_GENERATION)
    selected = {"api": first}
    host = _Host()
    marker = _authorize_current_story(host, first, story_controller)
    replacement.marker = copy.deepcopy(marker)
    replacement.enforced = True

    async def swap_after_execution(_task: Any, _services: Any) -> dict[str, Any]:
        selected["api"] = replacement
        return {"project": {"id": "must-not-cross-generation"}}

    first.execute_impl = swap_after_execution
    with _mounted(lambda: selected["api"]):
        result = await host._generate_creative_project({"text": "seed"})
    invalidate_external_bridge_cache(host)

    assert result is None
    assert len(first.tasks) == 1
    assert replacement.tasks == []
    assert host.legacy_calls == []


@pytest.mark.asyncio
async def test_target_marker_mismatch_after_source_commit_fails_closed(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    host = _Host()
    marker = _authorize_current_story(host, api, story_controller)
    api.marker = {**marker, "committed_at": 2.0}
    with _mounted(lambda: api):
        result = await host._generate_creative_project({"text": "seed"})
    invalidate_external_bridge_cache(host)

    assert result is None
    assert api.tasks == []
    assert host.legacy_calls == []
    assert story_controller.authority_state() == "blocked"


@pytest.mark.asyncio
async def test_story_callbacks_validate_copy_persist_and_acknowledge_replay() -> None:
    host = _CallbackHost()
    progress = _progress_payload()
    share = _share_payload()

    await host._content_story_record_progress(**progress)
    assert host.progress_calls[0]["project"]["title"] == "callback-only"
    assert progress["project"]["title"] == "A title"

    assert await host._content_story_offer_share(candidate=share) is True
    assert host.share_calls == [(share, False)]
    assert host.saved_sections == [{"users"}]
    assert host.data["users"]["friend"]["last_creative_share_key"] == share["key"]

    # A Content retry after its own CAS/save failure receives the durable
    # scheduling receipt without enqueuing the proactive candidate twice.
    assert await host._content_story_offer_share(candidate=share) is True
    assert len(host.share_calls) == 1
    assert host.saved_sections == [{"users"}]


@pytest.mark.asyncio
async def test_story_callbacks_reject_hostile_payloads_and_rollback_false_offer() -> None:
    host = _CallbackHost()
    progress = _progress_payload()
    progress["project"]["owner_id"] = "other-plugin"
    await host._content_story_record_progress(**progress)
    assert host.progress_calls == []

    hostile = {**_share_payload(), "local_path": "/must/not/cross"}
    assert await host._content_story_offer_share(candidate=hostile) is False
    assert host.share_calls == []

    host.schedule_result = False
    assert await host._content_story_offer_share(candidate=_share_payload()) is False
    assert "transient" not in host.data["users"]["friend"]
    assert "last_creative_share_key" not in host.data["users"]["friend"]
    assert host.saved_sections == []

    host.schedule_result = True

    def fail_save(*, sections: set[str]) -> None:
        raise OSError(f"injected-save-failure:{sorted(sections)}")

    host._save_data_sync = fail_save
    assert await host._content_story_offer_share(candidate=_share_payload()) is False
    assert "last_creative_share_key" not in host.data["users"]["friend"]


@pytest.mark.asyncio
async def test_model_budget_maps_roles_and_rejects_invalid_or_exhausted_requests() -> None:
    host = _Host()
    budget = _ContentStoryModelBudget(host, call_limit=8)
    cases = (
        ("creative_project", 500, "creative-provider"),
        ("creative_outline", 200, "outline-provider"),
        ("creative_review", 220, "review-provider"),
        ("creative_extract", 300, "review-provider"),
        ("creative_writing", 1360, "creative-provider"),
    )
    for role, maximum, provider in cases:
        assert await budget(provider_role=role, prompt="ok", max_tokens=maximum) == "accepted"
        assert host.model_calls[-1]["provider_id"] == provider

    before = len(host.model_calls)
    assert await budget(provider_role="unknown", prompt="ok", max_tokens=10) is None
    assert await budget(provider_role="creative_review", prompt="ok", max_tokens=221) is None
    assert await budget(provider_role="creative_review", prompt="\x00", max_tokens=20) is None
    assert len(host.model_calls) == before

    token_host = _Host()
    token_host._estimate_model_request_tokens = lambda *_args, **_kwargs: 5_000
    token_budget = _ContentStoryModelBudget(token_host, call_limit=8)
    for _ in range(_CONTENT_MODEL_EXECUTION_TOKEN_LIMIT // 5_000):
        assert await token_budget(
            provider_role="creative_project",
            prompt="ok",
            max_tokens=500,
        ) == "accepted"
    assert await token_budget(
        provider_role="creative_project",
        prompt="ok",
        max_tokens=500,
    ) is None
    assert len(token_host.model_calls) == 6


@pytest.mark.asyncio
async def test_failed_model_call_consumes_reserved_call_quota() -> None:
    host = _Host()
    host._llm_call = AsyncMock(side_effect=RuntimeError("provider body must not escape"))
    budget = _ContentStoryModelBudget(host, call_limit=1)
    with pytest.raises(RuntimeError):
        await budget(
            provider_role="creative_project",
            prompt="ok",
            max_tokens=500,
        )
    assert await budget(
        provider_role="creative_project",
        prompt="ok",
        max_tokens=500,
    ) is None
    host._llm_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_companion_provider_never_calls_astrbot_model() -> None:
    host = _Host()
    host.creative_provider_id = ""
    host.creative_outline_provider_id = ""
    host.creative_review_provider_id = ""
    host.mai_style_provider_id = ""
    host._llm_call = AsyncMock(return_value="must not run")
    budget = _ContentStoryModelBudget(host, call_limit=1)
    assert await budget(
        provider_role="creative_project",
        prompt="ok",
        max_tokens=500,
    ) is None
    host._llm_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_declared_unknown_contract_never_revives_legacy_owner_injection(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    api.capabilities = lambda: {**_descriptor(), "api_version": "content.story-api.v99"}
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        assert await host._maybe_start_creative_project(idle_checked=True) is False
    invalidate_external_bridge_cache(host)
    assert host.legacy_calls == []


@pytest.mark.asyncio
async def test_declared_execute_capability_with_missing_method_fails_closed(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    api.execute_task = None
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        assert await host._generate_creative_project({"text": "seed"}) is None
    invalidate_external_bridge_cache(host)
    assert host.legacy_calls == []


def test_partial_current_without_callable_descriptor_never_downgrades_to_legacy() -> None:
    api = SimpleNamespace(
        capabilities=None,
        versions=lambda: {},
        build_task=lambda value: value,
        execute_task=AsyncMock(return_value={}),
        maybe_start_creative_project=AsyncMock(return_value=True),
    )
    host = _Host()
    with _mounted(lambda: api):
        mode, selected, reason = host._content_story_contract()
    invalidate_external_bridge_cache(host)
    assert (mode, selected, reason) == (
        "incompatible",
        api,
        "descriptor_method_missing",
    )
    api.maybe_start_creative_project.assert_not_awaited()


def test_descriptorless_legacy_cache_refreshes_to_a_new_current_generation() -> None:
    legacy = SimpleNamespace(status=lambda: {"available": True})
    current = _CurrentAPI(generation=NEXT_GENERATION)
    selected = {"api": legacy}
    host = _Host()
    with _mounted(lambda: selected["api"]):
        assert host._content_companion_api() is legacy
        selected["api"] = current
        mode, api, reason = host._content_story_contract()
    invalidate_external_bridge_cache(host)
    assert (mode, api, reason) == ("current", current, "")


def test_content_without_callback_capabilities_is_explicitly_incompatible() -> None:
    api = _CurrentAPI()
    api.capabilities = lambda: {
        **_descriptor(),
        "capabilities": sorted(
            _CONTENT_REQUIRED_CAPABILITIES
            - {"story.callback.record-progress", "story.callback.offer-share"}
        ),
    }
    host = _Host()
    with _mounted(lambda: api):
        mode, selected, reason = host._content_story_contract()
    invalidate_external_bridge_cache(host)
    assert (mode, selected, reason) == (
        "incompatible",
        api,
        "descriptor_incompatible",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["disabled", "pending", "busy"])
async def test_current_advance_respects_existing_creative_runtime_gates(
    gate: str,
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    if gate == "disabled":
        host.enable_creative_writing = False
    elif gate == "pending":
        host._creative_has_pending_proactive_plan = lambda: True
    else:
        host._bot_currently_idle_for_creative_writing = lambda: False

    with _mounted(lambda: api):
        await host._maybe_advance_creative_projects()
    invalidate_external_bridge_cache(host)
    assert api.tasks == []
    assert api.services == []
    assert host.model_calls == []
    assert host.legacy_calls == []


@pytest.mark.asyncio
async def test_current_manual_edit_rejects_oversize_instead_of_truncating(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        rejected = await host._apply_creative_manual_edit(
            "story-1",
            "chunk_text",
            "x" * 901,
            "",
            0,
        )
        accepted = await host._apply_creative_manual_edit(
            "story-1",
            "chunk_text",
            "y" * 900,
            "",
            0,
        )
    invalidate_external_bridge_cache(host)

    assert rejected == {
        "success": False,
        "error": "story_edit_content_too_large",
    }
    assert len(api.tasks) == 1
    assert api.tasks[0]["recent_excerpt"] == "y" * 900
    assert accepted == {}
    assert host.legacy_calls == []


@pytest.mark.asyncio
async def test_current_business_failure_is_handled_without_legacy_retry(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()

    async def fail(_task: Any, _services: Any) -> dict[str, Any]:
        raise RuntimeError("sensitive upstream response")

    api.execute_impl = fail
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        assert await host._generate_creative_project({"text": "seed"}) is None
    invalidate_external_bridge_cache(host)
    assert host.legacy_calls == []


@pytest.mark.asyncio
async def test_caller_cancellation_crosses_current_boundary_unchanged(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()

    async def cancel(_task: Any, _services: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    api.execute_impl = cancel
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        with pytest.raises(asyncio.CancelledError):
            await host._content_story_execute("list")
    invalidate_external_bridge_cache(host)


@pytest.mark.asyncio
async def test_outer_cancellation_harvests_current_execution_before_propagating(
    story_controller: Any,
) -> None:
    api = _CurrentAPI()
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(_task: Any, _services: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"projects": []}

    api.execute_impl = execute
    host = _Host()
    _authorize_current_story(host, api, story_controller)
    with _mounted(lambda: api):
        operation = asyncio.create_task(host._content_story_execute("list"))
        await started.wait()
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    invalidate_external_bridge_cache(host)

    assert len(api.tasks) == 1
    assert host.legacy_calls == []


def test_closed_cached_generation_is_invalidated_and_current_generation_is_renegotiated() -> None:
    old = _CurrentAPI(generation=TARGET_GENERATION, state="closed")
    current = _CurrentAPI(generation=NEXT_GENERATION)
    selected = {"api": old}
    host = _Host()
    with _mounted(lambda: selected["api"]):
        assert host._content_companion_api() is old
        selected["api"] = current
        mode, api, reason = host._content_story_contract()
    invalidate_external_bridge_cache(host)
    assert (mode, api, reason) == ("current", current, "")


class _StrictProviderHarness(TokenBudgetMixin):
    def __init__(self) -> None:
        self.context = SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text="blocked response",
                    role="assistant",
                )
            )
        )

    @staticmethod
    def _resolve_chat_provider_id(provider_id: Any) -> str:
        return str(provider_id or "")

    @staticmethod
    def _classify_llm_prompt(_prompt: Any) -> str:
        return "test"

    @staticmethod
    def _is_llm_budget_exempt_task(_task: Any) -> bool:
        return False

    @staticmethod
    def _daily_token_soft_limit_should_defer(_task: Any) -> bool:
        return False

    @staticmethod
    def _llm_daily_budget_remaining() -> int:
        return 1000

    @staticmethod
    def _model_fallback_provider_for_call(**_kwargs: Any) -> tuple[str, str]:
        return "test", "fallback-provider"

    @staticmethod
    def _model_token_limit_route_for_call(**_kwargs: Any) -> tuple[bool, None, int]:
        return False, None, 100

    @staticmethod
    def _sensitive_model_replacement_provider(_provider: Any) -> str:
        return "replacement-provider"

    @staticmethod
    def _sensitive_model_replacement_keyword(text: Any) -> str:
        return "blocked" if "blocked" in str(text) else ""

    @staticmethod
    def _model_timeout_seconds_for_call(**_kwargs: Any) -> None:
        return None

    @staticmethod
    def _record_llm_usage(**_kwargs: Any) -> None:
        return None

    @staticmethod
    def _record_llm_budget_skip(**_kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_strict_provider_refusal_does_not_switch_to_sensitive_replacement() -> None:
    host = _StrictProviderHarness()
    assert await host._llm_call(
        "prompt",
        max_tokens=20,
        provider_id="fixed-provider",
        task="test",
        strict_provider=True,
    ) is None
    host.context.llm_generate.assert_awaited_once()
    assert host.context.llm_generate.await_args.kwargs["chat_provider_id"] == "fixed-provider"


@pytest.mark.asyncio
async def test_strict_tool_provider_refusal_does_not_switch_cards() -> None:
    host = _StrictProviderHarness()
    assert await host._llm_tool_call(
        "prompt",
        tools=[],
        max_tokens=20,
        provider_id="fixed-provider",
        task="test",
        strict_provider=True,
    ) is None
    host.context.llm_generate.assert_awaited_once()
    assert host.context.llm_generate.await_args.kwargs["chat_provider_id"] == "fixed-provider"
