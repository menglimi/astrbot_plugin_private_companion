from __future__ import annotations

import asyncio
import ast
import concurrent.futures
from contextlib import nullcontext
import inspect
from pathlib import Path
import re
import threading
from types import ModuleType, SimpleNamespace
from typing import Any
import uuid

import pytest

from story_migration_contract import (
    STORY_MIGRATION_API_FAMILY,
    STORY_MIGRATION_API_VERSION,
    STORY_MIGRATION_OWNER_ID,
    STORY_MIGRATION_SNAPSHOT_VERSION,
    StoryMigrationSnapshotError,
    build_story_migration_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


class _StoryAuthorityProbeError(RuntimeError):
    pass


EXPECTED_MANIFEST = (
    ("sync", "bridge_lifecycle_status", "self", "dict[str, Any]"),
    ("sync", "register_proactive_ability", "self, spec: dict[str, Any]", "bool"),
    ("sync", "unregister_proactive_ability", "self, name: str", "bool"),
    ("sync", "list_proactive_abilities", "self", "list[dict[str, Any]]"),
    ("async", "record_game_event", "self, payload: dict[str, Any]", "dict[str, Any]"),
    ("sync", "memory_page_capabilities", "self", "dict[str, Any]"),
    (
        "async",
        "export_memory_page_snapshot",
        "self, *, target_plugin_id: str, selected_date: str=''",
        "dict[str, Any]",
    ),
    (
        "async",
        "read_memory_page_photo",
        "self, *, target_plugin_id: str, photo_ref: str",
        "dict[str, Any]",
    ),
    ("sync", "get_realtime_voice_config", "self", "dict[str, Any]"),
    ("sync", "story_migration_capabilities", "self", "dict[str, Any]"),
    (
        "async",
        "export_story_migration_snapshot",
        "self, *, lease_token: str=''",
        "dict[str, Any]",
    ),
    (
        "async",
        "prepare_story_handoff",
        "self, *, target_plugin_id: str, owner_id: str",
        "dict[str, Any]",
    ),
    (
        "async",
        "abort_story_handoff",
        "self, *, lease_token: str",
        "dict[str, Any]",
    ),
    (
        "async",
        "commit_story_handoff",
        "self, *, lease_token: str=''",
        "dict[str, Any]",
    ),
    ("sync", "qzone_capabilities", "self", "dict[str, Any]"),
    ("sync", "qzone_status_snapshot", "self", "dict[str, Any]"),
    (
        "sync",
        "export_qzone_config_snapshot",
        "self, *, target_plugin_id: str",
        "dict[str, Any]",
    ),
    (
        "async",
        "execute_qzone_operation",
        "self, operation: str, payload: dict[str, Any]",
        "dict[str, Any]",
    ),
    (
        "async",
        "synthesize_realtime_voice",
        "self, text: str, *, tts_provider: Any=None, provider_settings: dict[str, Any] | None=None, source: str='external_realtime', play_local: bool=True",
        "dict[str, Any]",
    ),
    ("sync", "get_reality_touch_authorized_user_ids", "self", "list[str]"),
    ("async", "notify_mobile_location_update", "self, user_id: str", "dict[str, Any]"),
    ("sync", "get_reality_touch_host_context", "self, user_id: str", "dict[str, Any]"),
    ("sync", "export_reality_touch_legacy_state", "self", "dict[str, Any]"),
    ("async", "generate_reality_touch_text", "self, prompt: str, **kwargs: Any", "str"),
    ("async", "send_reality_touch_chat", "self, umo: str, text: str", "bool"),
    (
        "async",
        "record_reality_touch_output",
        "self, user_id: str, text: str, *, source: str='reality_touch_audio', delivered_at: float | None=None",
        "dict[str, Any]",
    ),
    ("sync", "get_reality_touch_cron_manager", "self", "Any | None"),
    ("async", "delete_reality_touch_cron_job", "self, job_id: str", "tuple[bool, str]"),
    ("sync", "get_bot_identity", "self", "dict[str, Any]"),
    ("sync", "get_unified_person_contract", "self", "dict[str, Any]"),
    ("sync", "resolve_unified_person", "self, identity: dict[str, Any]", "dict[str, Any]"),
    (
        "sync",
        "create_unified_person",
        "self, identity: dict[str, Any], *, profile: dict[str, Any] | None=None, operation_id: str=''",
        "dict[str, Any]",
    ),
    ("sync", "get_unified_person_projection", "self, person_id: str", "dict[str, Any] | None"),
    ("sync", "get_p6_readonly_status", "self", "dict[str, Any]"),
    ("sync", "get_unified_person_context", "self, event: Any | None=None", "dict[str, Any]"),
    ("sync", "get_scene_context", "self, user_id: str=''", "dict[str, Any]"),
    (
        "sync",
        "get_realtime_context",
        "self, user_id: str='', purpose: str='together'",
        "dict[str, Any]",
    ),
    (
        "sync",
        "record_external_realtime_continuity",
        "self, user_id: str, *, summary: str, public_summary: str='', facts: list[str] | None=None, ttl_seconds: int=21600, activity_id: str=''",
        "dict[str, Any]",
    ),
    (
        "sync",
        "get_external_realtime_continuity",
        "self, *, user_id: str='', public: bool=False",
        "dict[str, Any]",
    ),
    (
        "sync",
        "notify_external_activity_started",
        "self, activity_id: str, *, user_id: str='', kind: str='external', label: str='', source_plugin: str='external', ttl_seconds: int=240, metadata: dict[str, Any] | None=None",
        "dict[str, Any]",
    ),
    (
        "sync",
        "notify_external_activity_updated",
        "self, activity_id: str, *, user_id: str='', kind: str='', label: str='', source_plugin: str='', ttl_seconds: int=240, metadata: dict[str, Any] | None=None",
        "dict[str, Any]",
    ),
    ("sync", "notify_external_activity_ended", "self, activity_id: str", "bool"),
    (
        "sync",
        "get_external_activity",
        "self, *, user_id: str='', activity_id: str=''",
        "dict[str, Any]",
    ),
    (
        "async",
        "prepare_proactive_chat",
        "self, session_id: str, *, unanswered_count: int=0",
        "dict[str, Any]",
    ),
    (
        "async",
        "review_proactive_chat_message",
        "self, session_id: str, text: str, *, token: str=''",
        "dict[str, Any]",
    ),
    (
        "async",
        "notify_proactive_chat_sent",
        "self, session_id: str, text: str, *, token: str=''",
        "dict[str, Any]",
    ),
    ("async", "cancel_proactive_chat", "self, session_id: str, *, token: str=''", "bool"),
    ("sync", "resolve_historical_chat_identities", "self, speakers: list[str]", "dict[str, Any]"),
    (
        "async",
        "stage_historical_relationship_observations",
        "self, *, user_id: str, user_name: str, batch_id: str, observations: list[dict[str, Any]]",
        "dict[str, Any]",
    ),
    (
        "async",
        "rebind_historical_relationship_observations",
        "self, *, batch_id: str, old_user_id: str, user_id: str, user_name: str=''",
        "dict[str, Any]",
    ),
    (
        "async",
        "rollback_historical_relationship_observations",
        "self, batch_id: str",
        "dict[str, Any]",
    ),
)

DOMAIN_METHODS = {
    "identity": {
        "get_reality_touch_authorized_user_ids",
        "get_bot_identity",
        "get_unified_person_contract",
        "resolve_unified_person",
        "create_unified_person",
        "get_unified_person_projection",
        "get_unified_person_context",
        "resolve_historical_chat_identities",
    },
    "relationship": {
        "get_reality_touch_host_context",
        "stage_historical_relationship_observations",
        "rebind_historical_relationship_observations",
        "rollback_historical_relationship_observations",
    },
    "scheduler": {
        "register_proactive_ability",
        "unregister_proactive_ability",
        "list_proactive_abilities",
        "notify_mobile_location_update",
        "get_reality_touch_cron_manager",
        "delete_reality_touch_cron_job",
        "notify_external_activity_started",
        "notify_external_activity_updated",
        "notify_external_activity_ended",
        "get_external_activity",
        "prepare_proactive_chat",
        "review_proactive_chat_message",
        "notify_proactive_chat_sent",
        "cancel_proactive_chat",
    },
    "memory": {
        "record_game_event",
        "memory_page_capabilities",
        "export_memory_page_snapshot",
        "read_memory_page_photo",
        "record_external_realtime_continuity",
        "get_external_realtime_continuity",
    },
    "content": {
        "get_realtime_voice_config",
        "story_migration_capabilities",
        "export_story_migration_snapshot",
        "prepare_story_handoff",
        "abort_story_handoff",
        "commit_story_handoff",
        "synthesize_realtime_voice",
        "generate_reality_touch_text",
        "send_reality_touch_chat",
        "record_reality_touch_output",
    },
    "diagnostics": {
        "bridge_lifecycle_status",
        "export_reality_touch_legacy_state",
        "get_p6_readonly_status",
        "get_scene_context",
        "get_realtime_context",
    },
    "image": set(),
    "qzone": {
        "qzone_capabilities",
        "qzone_status_snapshot",
        "export_qzone_config_snapshot",
        "execute_qzone_operation",
    },
}

FAMILY_CLASSES = {
    "identity": "_IdentityCapabilityFamily",
    "relationship": "_RelationshipCapabilityFamily",
    "scheduler": "_SchedulerCapabilityFamily",
    "memory": "_MemoryCapabilityFamily",
    "content": "_ContentCapabilityFamily",
    "diagnostics": "_DiagnosticsCapabilityFamily",
    "image": "_ImageCapabilityFamily",
    "qzone": "_QzoneCapabilityFamily",
}


class _AuthorityProbe:
    def authority_state(self) -> str:
        return "open"

    def stage_generation(self, _generation: str) -> None:
        return None

    def activate_generation(self, _generation: str) -> None:
        return None

    def supersede_generation(self, _generation: str) -> None:
        return None

    def close_generation(self, _generation: str) -> None:
        return None

    def authority_state(self) -> str:
        return "active"


_AUTHORITY_PROBE = _AuthorityProbe()


FACADE_KERNELS = {
    "bridge_lifecycle_status",
    "synthesize_realtime_voice",
    "generate_reality_touch_text",
    "export_reality_touch_legacy_state",
    "prepare_proactive_chat",
    "review_proactive_chat_message",
    "notify_proactive_chat_sent",
    "cancel_proactive_chat",
}


def _tree(filename: str) -> ast.Module:
    return ast.parse((ROOT / filename).read_text(encoding="utf-8"), filename=filename)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _methods(owner: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _isolated_class(filename: str, class_name: str, namespace: dict[str, Any]) -> type:
    class_node = _class(_tree(filename), class_name)
    module = ast.Module(body=[class_node], type_ignores=[])
    ast.fix_missing_locations(module)
    isolated_namespace = {
        "Any": Any,
        "StoryAuthorityError": _StoryAuthorityProbeError,
        "assert_single_persona_story_shelf": lambda _plugin: None,
        "story_authority_controller": lambda: _AUTHORITY_PROBE,
        "story_profile_inspection_context": nullcontext,
        **namespace,
    }
    exec(compile(module, filename, "exec"), isolated_namespace)
    return isolated_namespace[class_name]


def test_facade_manifest_is_frozen_and_explicit() -> None:
    owner = _class(_tree("main.py"), "PrivateCompanionExtensionAPI")
    public = [
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    manifest = tuple(
        (
            "async" if isinstance(node, ast.AsyncFunctionDef) else "sync",
            node.name,
            ast.unparse(node.args),
            ast.unparse(node.returns),
        )
        for node in public
    )

    assert manifest == EXPECTED_MANIFEST
    assert len(public) == 51
    assert sum(isinstance(node, ast.FunctionDef) for node in public) == 30
    assert sum(isinstance(node, ast.AsyncFunctionDef) for node in public) == 21
    assert "__getattr__" not in _methods(owner)
    assert not owner.bases


def test_capability_families_are_owner_only_and_match_frozen_domains() -> None:
    all_methods = set().union(*DOMAIN_METHODS.values())
    assert all_methods == {item[1] for item in EXPECTED_MANIFEST}
    assert {name: len(methods) for name, methods in DOMAIN_METHODS.items()} == {
        "identity": 8,
        "relationship": 4,
        "scheduler": 14,
        "memory": 6,
        "content": 10,
        "diagnostics": 5,
        "image": 0,
        "qzone": 4,
    }

    moved = set()
    facade_methods = _methods(_class(_tree("main.py"), "PrivateCompanionExtensionAPI"))
    for family, class_name in FAMILY_CLASSES.items():
        tree = _tree(f"extension_api_{family}.py")
        owner = _class(tree, class_name)
        methods = _methods(owner)
        slots = next(
            node
            for node in owner.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets)
        )
        assert ast.literal_eval(slots.value) == ("_owner",)
        assert "__getattr__" not in methods
        assert not owner.bases
        assert not {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } & {"main", *(f"extension_api_{name}" for name in FAMILY_CLASSES)}
        assert not {
            node.func.id
            for node in ast.walk(owner)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } & {"setattr", "exec", "eval", "__import__"}

        stored_attributes = {
            node.attr
            for node in ast.walk(owner)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        assert stored_attributes == {"_owner"}

        public_family_methods = {name for name in methods if not name.startswith("_")}
        assert public_family_methods <= DOMAIN_METHODS[family]
        for name in public_family_methods:
            assert type(methods[name]) is type(facade_methods[name])
            assert ast.dump(methods[name].args, include_attributes=False) == ast.dump(
                facade_methods[name].args, include_attributes=False
            )
            assert ast.dump(methods[name].returns, include_attributes=False) == ast.dump(
                facade_methods[name].returns, include_attributes=False
            )
        moved.update(public_family_methods)

    assert moved == all_methods - FACADE_KERNELS
    assert len(moved) == 43


def test_moved_methods_are_thin_wrappers_and_kernels_remain_in_facade() -> None:
    owner = _class(_tree("main.py"), "PrivateCompanionExtensionAPI")
    methods = _methods(owner)
    moved = set().union(
        *(
            {
                name
                for name in _methods(
                    _class(_tree(f"extension_api_{family}.py"), class_name)
                )
                if not name.startswith("_")
            }
            for family, class_name in FAMILY_CLASSES.items()
        )
    )

    for name in moved:
        body = methods[name].body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        assert len(body) == 1
        assert isinstance(body[0], ast.Return)
        assert isinstance(body[0].value, (ast.Call, ast.Await))

    for name in FACADE_KERNELS:
        source = ast.unparse(methods[name])
        assert "_family" not in source
        assert "self._plugin" in source


def test_cross_family_composition_still_dispatches_through_facade() -> None:
    diagnostics = _methods(
        _class(_tree("extension_api_diagnostics.py"), "_DiagnosticsCapabilityFamily")
    )["get_realtime_context"]
    diagnostics_source = ast.unparse(diagnostics)
    for method_name in (
        "get_scene_context",
        "get_external_activity",
        "get_external_realtime_continuity",
        "get_bot_identity",
    ):
        assert f"self._owner.{method_name}" in diagnostics_source

    identity = _methods(
        _class(_tree("extension_api_identity.py"), "_IdentityCapabilityFamily")
    )["resolve_historical_chat_identities"]
    assert "self._owner.get_bot_identity" in ast.unparse(identity)


def test_cross_family_composition_uses_latest_facade_overrides_at_runtime() -> None:
    single_line = lambda value, limit: str(value or "").strip()[:limit]
    diagnostics_type = _isolated_class(
        "extension_api_diagnostics.py",
        "_DiagnosticsCapabilityFamily",
        {
            "_single_line": single_line,
            "build_p6_readonly_status": lambda value: value,
        },
    )
    plugin = SimpleNamespace(
        _format_companion_scene_snapshot=lambda snapshot, *, purpose: (
            f"scene={snapshot['value']};purpose={purpose}"
        )
    )
    owner = SimpleNamespace(
        _plugin=plugin,
        get_scene_context=lambda _user_id: {"value": "stale"},
        get_external_activity=lambda **_kwargs: {},
        get_external_realtime_continuity=lambda **_kwargs: {},
        get_bot_identity=lambda: {"name": "stale"},
    )
    diagnostics = diagnostics_type(owner)
    owner.get_scene_context = lambda _user_id: {"value": "fresh"}
    owner.get_external_activity = lambda **_kwargs: {
        "kind": "shared_watch",
        "label": "fresh activity",
    }
    owner.get_external_realtime_continuity = lambda **_kwargs: {
        "summary": "fresh continuity"
    }
    owner.get_bot_identity = lambda: {"name": "fresh bot"}

    context = diagnostics.get_realtime_context("user", purpose="runtime")

    assert context["snapshot"] == {"value": "fresh"}
    assert context["external_activity"]["label"] == "fresh activity"
    assert context["realtime_continuity"]["summary"] == "fresh continuity"
    assert context["bot"] == {"name": "fresh bot"}
    assert "fresh activity" in context["prompt"]
    assert "fresh continuity" in context["prompt"]

    identity_type = _isolated_class(
        "extension_api_identity.py",
        "_IdentityCapabilityFamily",
        {"_single_line": single_line, "re": re},
    )
    identity_owner = SimpleNamespace(
        _plugin=SimpleNamespace(
            data={"users": {}},
            _resolve_worldbook_member_by_name=lambda _label: [],
            _configured_target_ids=lambda: [],
        ),
        get_bot_identity=lambda: {"name": "stale"},
    )
    identity = identity_type(identity_owner)
    identity_owner.get_bot_identity = lambda: {
        "name": "fresh",
        "aliases": ["new"],
        "self_ids": ["bot-id"],
        "selected_id": "bot-id",
        "qq_id": "",
    }

    resolved = identity.resolve_historical_chat_identities(["speaker"])

    assert resolved["bot"] == {
        "name": "fresh",
        "aliases": ["new"],
        "self_ids": ["bot-id"],
        "selected_id": "bot-id",
        "qq_id": "",
    }


def test_every_moved_wrapper_preserves_result_and_exception_identity() -> None:
    facade_node = _class(_tree("main.py"), "PrivateCompanionExtensionAPI")

    class FamilyProbe:
        __slots__ = ("_owner",)

        def __init__(self, owner: Any) -> None:
            self._owner = owner

    namespace = {
        "Any": Any,
        "threading": threading,
        "uuid": uuid,
        "MemoryPageSnapshotService": lambda _owner: SimpleNamespace(
            clear_references=lambda: None
        ),
        "story_authority_controller": lambda: _AUTHORITY_PROBE,
        **{class_name: FamilyProbe for class_name in FAMILY_CLASSES.values()},
    }
    module = ast.Module(body=[facade_node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "main.py", "exec"), namespace)
    facade_type = namespace["PrivateCompanionExtensionAPI"]
    facade_methods = _methods(facade_node)
    moved = set().union(*DOMAIN_METHODS.values()) - FACADE_KERNELS

    for name in moved:
        method_node = facade_methods[name]
        body = list(method_node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body.pop(0)
        returned = body[0].value
        call = returned.value if isinstance(returned, ast.Await) else returned
        family_attribute = call.func.value.attr
        expected_async = isinstance(method_node, ast.AsyncFunctionDef)

        captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        sentinel = object()
        if expected_async:
            async def return_sentinel(
                _family_self: Any, *args: Any, **kwargs: Any
            ) -> Any:
                captured.append((args, kwargs))
                return sentinel
        else:
            def return_sentinel(
                _family_self: Any, *args: Any, **kwargs: Any
            ) -> Any:
                captured.append((args, kwargs))
                return sentinel
        setattr(FamilyProbe, name, return_sentinel)

        facade = facade_type(object())
        signature = inspect.signature(getattr(facade_type, name))
        positional: list[Any] = []
        keywords: dict[str, Any] = {}
        for parameter in list(signature.parameters.values())[1:]:
            value = object()
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                positional.append(value)
            elif parameter.kind is inspect.Parameter.KEYWORD_ONLY:
                keywords[parameter.name] = value

        result = getattr(facade, name)(*positional, **keywords)
        if expected_async:
            result = asyncio.run(result)
        assert result is sentinel
        assert captured == [(tuple(positional), keywords)]
        assert getattr(facade, family_attribute)._owner is facade

        failure = RuntimeError(f"family-failure:{name}")
        if expected_async:
            async def raise_failure(
                _family_self: Any, *unused_args: Any, **unused_kwargs: Any
            ) -> Any:
                raise failure
        else:
            def raise_failure(
                _family_self: Any, *unused_args: Any, **unused_kwargs: Any
            ) -> Any:
                raise failure
        setattr(FamilyProbe, name, raise_failure)

        try:
            result = getattr(facade, name)(*positional, **keywords)
            if expected_async:
                asyncio.run(result)
        except RuntimeError as error:
            assert error is failure
        else:
            raise AssertionError(f"{name} swallowed the family exception")


def test_story_migration_facade_state_machine_is_one_way_and_activation_is_boolean() -> None:
    facade_node = _class(_tree("main.py"), "PrivateCompanionExtensionAPI")

    class FamilyProbe:
        __slots__ = ("_owner",)

        def __init__(self, owner: Any) -> None:
            self._owner = owner

    namespace = {
        "Any": Any,
        "threading": threading,
        "uuid": uuid,
        "MemoryPageSnapshotService": lambda _owner: SimpleNamespace(
            clear_references=lambda: None
        ),
        "story_authority_controller": lambda: _AUTHORITY_PROBE,
        **{class_name: FamilyProbe for class_name in FAMILY_CLASSES.values()},
    }
    module = ast.Module(body=[facade_node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "main.py", "exec"), namespace)
    facade_type = namespace["PrivateCompanionExtensionAPI"]

    ready = facade_type(object())
    assert ready._story_migration_lifecycle_state() == "created"
    assert ready._activate_story_migration_api() is True
    assert ready._story_migration_lifecycle_state() == "ready"
    assert ready._activate_story_migration_api() is False
    ready._supersede_story_migration_api()
    assert ready._story_migration_lifecycle_state() == "superseded"
    ready._close_story_migration_api()
    assert ready._story_migration_lifecycle_state() == "superseded"

    closed = facade_type(object())
    closed._close_story_migration_api()
    assert closed._story_migration_lifecycle_state() == "closed"
    assert closed._activate_story_migration_api() is False


def test_story_snapshot_family_descriptor_export_and_lifecycle_recheck() -> None:
    family_type = _isolated_class(
        "extension_api_content.py",
        "_ContentCapabilityFamily",
        {
            "STORY_MIGRATION_API_FAMILY": STORY_MIGRATION_API_FAMILY,
            "STORY_MIGRATION_API_VERSION": STORY_MIGRATION_API_VERSION,
            "STORY_MIGRATION_OWNER_ID": STORY_MIGRATION_OWNER_ID,
            "STORY_MIGRATION_SNAPSHOT_VERSION": STORY_MIGRATION_SNAPSHOT_VERSION,
            "StoryMigrationSnapshotError": StoryMigrationSnapshotError,
            "build_story_migration_snapshot": build_story_migration_snapshot,
            "_STORY_SNAPSHOT_ADMISSION": threading.BoundedSemaphore(1),
            "_STORY_SNAPSHOT_EXECUTOR": None,
            "asyncio": asyncio,
            "_single_line": lambda value, limit: str(value or "")[:limit],
            "Plain": object,
            "story_authority_controller": lambda: _AUTHORITY_PROBE,
        },
    )
    state = {"value": "created"}
    plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-1", "title": "雨声"}]},
        _data_lock=asyncio.Lock(),
    )
    owner = SimpleNamespace(
        _plugin=plugin,
        _story_migration_lifecycle_state=lambda: state["value"],
        _story_migration_instance_generation=lambda: "generation-1",
    )
    family = family_type(owner)

    created = family.story_migration_capabilities()
    assert set(created) == {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "supported_task_versions",
        "capabilities",
        "lifecycle_state",
        "degraded_reasons",
    }
    assert created == {
        "plugin_id": STORY_MIGRATION_OWNER_ID,
        "instance_generation": "generation-1",
        "api_family": STORY_MIGRATION_API_FAMILY,
        "api_version": STORY_MIGRATION_API_VERSION,
        "supported_task_versions": [STORY_MIGRATION_SNAPSHOT_VERSION],
        "capabilities": [
            "story.snapshot.export",
            "story.snapshot.path-free",
            "story.snapshot.read-only",
            "story.handoff.prepare",
            "story.handoff.export-lease",
            "story.handoff.abort",
            "story.handoff.commit",
        ],
        "lifecycle_state": "created",
        "degraded_reasons": ["story_snapshot_service_not_ready"],
    }
    created["capabilities"].append("caller-mutation")
    assert "caller-mutation" not in family.story_migration_capabilities()["capabilities"]

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        asyncio.run(family.export_story_migration_snapshot())
    assert captured.value.code == "story_snapshot_service_closed"

    state["value"] = "ready"
    exported = asyncio.run(family.export_story_migration_snapshot())
    assert exported["projects"][0]["title"] == "雨声"
    exported["projects"][0]["title"] = "caller mutation"
    assert plugin.data["creative_projects"][0]["title"] == "雨声"

    async def close_while_waiting_for_lock() -> str:
        await plugin._data_lock.acquire()
        task = asyncio.create_task(family.export_story_migration_snapshot())
        await asyncio.sleep(0)
        state["value"] = "superseded"
        plugin._data_lock.release()
        with pytest.raises(StoryMigrationSnapshotError) as waiting_failure:
            await task
        return waiting_failure.value.code

    assert asyncio.run(close_while_waiting_for_lock()) == "story_snapshot_service_closed"


def test_story_snapshot_runtime_has_process_stable_single_worker_bound() -> None:
    tree = _tree("extension_api_content.py")
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_new_story_snapshot_runtime"
    )
    executor_call = next(
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ThreadPoolExecutor"
    )
    keywords = {item.arg: item.value for item in executor_call.keywords}
    assert ast.literal_eval(keywords["max_workers"]) == 1
    admission_call = next(
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "threading"
        and node.func.attr == "BoundedSemaphore"
    )
    assert ast.literal_eval(admission_call.args[0]) == 1
    runtime_key = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_STORY_SNAPSHOT_RUNTIME_KEY"
            for target in node.targets
        )
    )
    assert ast.literal_eval(runtime_key) == (
        "_astrbot_private_companion_story_snapshot_runtime_v1"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setdefault"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "sys"
        and node.func.value.attr == "modules"
        for node in ast.walk(tree)
    )


def test_story_snapshot_runtime_first_load_race_keeps_one_runtime_and_retires_loser() -> None:
    tree = _tree("extension_api_content.py")
    installer_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_install_story_snapshot_runtime"
    )
    module = ast.Module(body=[installer_node], type_ignores=[])
    ast.fix_missing_locations(module)

    runtime_key = f"_story_snapshot_race_{uuid.uuid4().hex}"
    shared_modules: dict[str, Any] = {}
    barrier = threading.Barrier(2)
    made: list[ModuleType] = []
    retired: list[tuple[ModuleType, bool, bool]] = []
    records_lock = threading.Lock()

    class FakeExecutor:
        def __init__(self, runtime: ModuleType) -> None:
            self.runtime = runtime

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            with records_lock:
                retired.append((self.runtime, wait, cancel_futures))

    def new_runtime() -> ModuleType:
        runtime = ModuleType(f"candidate-{threading.get_ident()}")
        runtime.admission = object()
        runtime.executor = FakeExecutor(runtime)
        with records_lock:
            made.append(runtime)
        barrier.wait(timeout=2)
        return runtime

    def installer() -> Any:
        namespace = {
            "ModuleType": ModuleType,
            "_new_story_snapshot_runtime": new_runtime,
            "_STORY_SNAPSHOT_RUNTIME_KEY": runtime_key,
            "sys": SimpleNamespace(modules=shared_modules),
        }
        exec(compile(module, "extension_api_content.py", "exec"), namespace)
        return namespace["_install_story_snapshot_runtime"]()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(installer)
        second = pool.submit(installer)
        first_runtime = first.result(timeout=2)
        second_runtime = second.result(timeout=2)

    assert first_runtime is second_runtime is shared_modules[runtime_key]
    assert len(made) == 2
    assert retired == [
        (
            next(runtime for runtime in made if runtime is not first_runtime),
            False,
            True,
        )
    ]
    assert first_runtime not in {record[0] for record in retired}


@pytest.mark.parametrize("invalidated", ["state", "generation"])
def test_story_snapshot_worker_rechecks_exact_instance_after_build(invalidated: str) -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_build(projects):
        entered.set()
        assert release.wait(2)
        return build_story_migration_snapshot(projects)

    family_type = _isolated_class(
        "extension_api_content.py",
        "_ContentCapabilityFamily",
        {
            "STORY_MIGRATION_API_FAMILY": STORY_MIGRATION_API_FAMILY,
            "STORY_MIGRATION_API_VERSION": STORY_MIGRATION_API_VERSION,
            "STORY_MIGRATION_OWNER_ID": STORY_MIGRATION_OWNER_ID,
            "STORY_MIGRATION_SNAPSHOT_VERSION": STORY_MIGRATION_SNAPSHOT_VERSION,
            "StoryMigrationSnapshotError": StoryMigrationSnapshotError,
            "build_story_migration_snapshot": slow_build,
            "_STORY_SNAPSHOT_ADMISSION": threading.BoundedSemaphore(1),
            "_STORY_SNAPSHOT_EXECUTOR": None,
            "asyncio": asyncio,
            "_single_line": lambda value, limit: str(value or "")[:limit],
            "Plain": object,
        },
    )
    lifecycle = {"state": "ready", "generation": "generation-1"}
    plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-1"}]},
        _data_lock=asyncio.Lock(),
    )
    owner = SimpleNamespace(
        _plugin=plugin,
        _story_migration_lifecycle_state=lambda: lifecycle["state"],
        _story_migration_instance_generation=lambda: lifecycle["generation"],
    )
    family = family_type(owner)

    async def scenario() -> str:
        task = asyncio.create_task(family.export_story_migration_snapshot())
        while not entered.is_set():
            await asyncio.sleep(0)
        if invalidated == "state":
            lifecycle["state"] = "superseded"
        else:
            lifecycle["generation"] = "generation-2"
        release.set()
        with pytest.raises(StoryMigrationSnapshotError) as captured:
            await task
        return captured.value.code

    assert asyncio.run(scenario()) == "story_snapshot_service_closed"


def test_story_snapshot_cancellation_harvests_worker_before_unlocking_source() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_build(projects):
        entered.set()
        assert release.wait(2)
        return build_story_migration_snapshot(projects)

    family_type = _isolated_class(
        "extension_api_content.py",
        "_ContentCapabilityFamily",
        {
            "STORY_MIGRATION_API_FAMILY": STORY_MIGRATION_API_FAMILY,
            "STORY_MIGRATION_API_VERSION": STORY_MIGRATION_API_VERSION,
            "STORY_MIGRATION_OWNER_ID": STORY_MIGRATION_OWNER_ID,
            "STORY_MIGRATION_SNAPSHOT_VERSION": STORY_MIGRATION_SNAPSHOT_VERSION,
            "StoryMigrationSnapshotError": StoryMigrationSnapshotError,
            "build_story_migration_snapshot": slow_build,
            "_STORY_SNAPSHOT_ADMISSION": threading.BoundedSemaphore(1),
            "_STORY_SNAPSHOT_EXECUTOR": None,
            "asyncio": asyncio,
            "_single_line": lambda value, limit: str(value or "")[:limit],
            "Plain": object,
        },
    )
    plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-1", "title": "before"}]},
        _data_lock=asyncio.Lock(),
    )
    owner = SimpleNamespace(
        _plugin=plugin,
        _story_migration_lifecycle_state=lambda: "ready",
        _story_migration_instance_generation=lambda: "generation-1",
    )
    family = family_type(owner)

    async def scenario() -> None:
        export = asyncio.create_task(family.export_story_migration_snapshot())
        while not entered.is_set():
            await asyncio.sleep(0)

        async def mutate_source() -> None:
            async with plugin._data_lock:
                plugin.data["creative_projects"][0]["title"] = "after"

        mutation = asyncio.create_task(mutate_source())
        export.cancel()
        export.cancel()
        await asyncio.sleep(0)
        assert plugin._data_lock.locked()
        assert not mutation.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await export
        await mutation
        assert plugin.data["creative_projects"][0]["title"] == "after"
        assert (await family.export_story_migration_snapshot())["projects"][0][
            "title"
        ] == "after"

    asyncio.run(scenario())


def test_story_snapshot_flood_fails_fast_across_generations_without_queueing() -> None:
    entered = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    counts = {"active": 0, "maximum": 0, "calls": 0}

    def tracked_build(projects):
        with counter_lock:
            counts["active"] += 1
            counts["calls"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
        entered.set()
        assert release.wait(2)
        try:
            return build_story_migration_snapshot(projects)
        finally:
            with counter_lock:
                counts["active"] -= 1

    admission = threading.BoundedSemaphore(1)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    family_type = _isolated_class(
        "extension_api_content.py",
        "_ContentCapabilityFamily",
        {
            "STORY_MIGRATION_API_FAMILY": STORY_MIGRATION_API_FAMILY,
            "STORY_MIGRATION_API_VERSION": STORY_MIGRATION_API_VERSION,
            "STORY_MIGRATION_OWNER_ID": STORY_MIGRATION_OWNER_ID,
            "STORY_MIGRATION_SNAPSHOT_VERSION": STORY_MIGRATION_SNAPSHOT_VERSION,
            "StoryMigrationSnapshotError": StoryMigrationSnapshotError,
            "build_story_migration_snapshot": tracked_build,
            "_STORY_SNAPSHOT_ADMISSION": admission,
            "_STORY_SNAPSHOT_EXECUTOR": executor,
            "asyncio": asyncio,
            "_single_line": lambda value, limit: str(value or "")[:limit],
            "Plain": object,
        },
    )
    first_plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-1"}]},
        _data_lock=asyncio.Lock(),
    )
    second_plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-2"}]},
        _data_lock=asyncio.Lock(),
    )
    first_owner = SimpleNamespace(
        _plugin=first_plugin,
        _story_migration_lifecycle_state=lambda: "ready",
        _story_migration_instance_generation=lambda: "generation-1",
    )
    second_owner = SimpleNamespace(
        _plugin=second_plugin,
        _story_migration_lifecycle_state=lambda: "ready",
        _story_migration_instance_generation=lambda: "generation-2",
    )
    first_family = family_type(first_owner)
    second_family = family_type(second_owner)

    async def scenario() -> None:
        first = asyncio.create_task(first_family.export_story_migration_snapshot())
        while not entered.is_set():
            await asyncio.sleep(0)
        overflow = [
            *(
                asyncio.create_task(first_family.export_story_migration_snapshot())
                for _ in range(20)
            ),
            *(
                asyncio.create_task(second_family.export_story_migration_snapshot())
                for _ in range(20)
            ),
        ]
        failures = await asyncio.wait_for(
            asyncio.gather(*overflow, return_exceptions=True),
            timeout=1,
        )
        assert all(
            isinstance(error, StoryMigrationSnapshotError)
            and error.code == "story_snapshot_busy"
            and str(error) == "story_snapshot_busy"
            for error in failures
        )
        assert first_plugin._data_lock.locked()
        assert not second_plugin._data_lock.locked()
        assert counts == {"active": 1, "maximum": 1, "calls": 1}
        assert executor._work_queue.qsize() == 0
        release.set()
        assert (await first)["projects"][0]["id"] == "work-1"
        assert (await second_family.export_story_migration_snapshot())[
            "projects"
        ][0]["id"] == "work-2"

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        executor.shutdown(wait=True)
    assert counts == {"active": 0, "maximum": 1, "calls": 2}


def test_story_snapshot_native_worker_failure_is_body_free_and_releases_admission() -> None:
    secret = "DO-NOT-ECHO-WORKER-DETAIL"

    def explode(_projects):
        raise RuntimeError(secret)

    admission = threading.BoundedSemaphore(1)
    family_type = _isolated_class(
        "extension_api_content.py",
        "_ContentCapabilityFamily",
        {
            "STORY_MIGRATION_API_FAMILY": STORY_MIGRATION_API_FAMILY,
            "STORY_MIGRATION_API_VERSION": STORY_MIGRATION_API_VERSION,
            "STORY_MIGRATION_OWNER_ID": STORY_MIGRATION_OWNER_ID,
            "STORY_MIGRATION_SNAPSHOT_VERSION": STORY_MIGRATION_SNAPSHOT_VERSION,
            "StoryMigrationSnapshotError": StoryMigrationSnapshotError,
            "build_story_migration_snapshot": explode,
            "_STORY_SNAPSHOT_ADMISSION": admission,
            "_STORY_SNAPSHOT_EXECUTOR": None,
            "asyncio": asyncio,
            "_single_line": lambda value, limit: str(value or "")[:limit],
            "Plain": object,
        },
    )
    plugin = SimpleNamespace(
        data={"creative_projects": [{"id": "work-1"}]},
        _data_lock=asyncio.Lock(),
    )
    owner = SimpleNamespace(
        _plugin=plugin,
        _story_migration_lifecycle_state=lambda: "ready",
        _story_migration_instance_generation=lambda: "generation-1",
    )

    with pytest.raises(StoryMigrationSnapshotError) as captured:
        asyncio.run(family_type(owner).export_story_migration_snapshot())
    assert captured.value.code == "story_snapshot_build_failed"
    assert str(captured.value) == captured.value.code
    assert secret not in str(captured.value)
    assert admission.acquire(blocking=False)
    admission.release()
