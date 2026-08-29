from __future__ import annotations

import ast
import asyncio
import copy
from itertools import combinations
from pathlib import Path
import types
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COOPERATIVE_CONTENT_METHODS = frozenset(
    {
        "_apply_creative_manual_edit",
        "_generate_creative_chunk",
        "_generate_creative_project",
        "_maybe_advance_creative_projects",
        "_maybe_generate_creative_cover",
        "_maybe_start_creative_project",
        "_rebuild_creative_memory_from_project",
        "_review_creative_chunk",
    }
)


def _source_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(
            part in {".git", "__pycache__", "dist", "scripts", "tests"}
            for part in relative.parts
        ):
            continue
        result.append(path)
    return sorted(result)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _methods(owner: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _property_accessor_kind(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> str:
    kinds: list[str] = []
    for decorator in method.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "property":
            kinds.append("getter")
        elif (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id == name
            and decorator.attr in {"setter", "deleter"}
        ):
            kinds.append(decorator.attr)
    return kinds[0] if len(kinds) == 1 else ""


def _is_property_descriptor_chain(
    name: str,
    definitions: list[ast.FunctionDef | ast.AsyncFunctionDef],
) -> bool:
    kinds = [_property_accessor_kind(method, name) for method in definitions]
    return (
        kinds.count("getter") == 1
        and all(kind in {"getter", "setter", "deleter"} for kind in kinds)
        and len(kinds) == len(set(kinds))
    )


def _plugin_base_sources() -> tuple[list[str], dict[str, tuple[Path, ast.ClassDef]]]:
    main_tree = _tree(ROOT / "main.py")
    plugin = next(
        node
        for node in main_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    base_names = [ast.unparse(base) for base in plugin.bases]
    imports: dict[str, Path] = {}
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        module_path = ROOT.joinpath(*node.module.split(".")).with_suffix(".py")
        if not module_path.is_file():
            continue
        for alias in node.names:
            imports[alias.asname or alias.name] = module_path

    resolved: dict[str, tuple[Path, ast.ClassDef]] = {}
    for base_name in base_names:
        path = imports.get(base_name)
        if path is not None:
            resolved[base_name] = (path, _top_level_class(path, base_name))
    return base_names, resolved


def _cooperative_super_targets(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    targets: list[str] = []
    for call in (node for node in ast.walk(method) if isinstance(node, ast.Call)):
        function = call.func
        if not isinstance(function, ast.Attribute):
            continue
        receiver = function.value
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
        ):
            targets.append(function.attr)
    return targets


def test_production_class_bodies_do_not_redefine_ordinary_methods() -> None:
    collisions: list[str] = []
    for path in _source_files():
        for owner in (
            node
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.ClassDef)
        ):
            definitions: dict[
                str,
                list[ast.FunctionDef | ast.AsyncFunctionDef],
            ] = {}
            for method in owner.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.setdefault(method.name, []).append(method)
            for name, repeated in definitions.items():
                if len(repeated) < 2 or _is_property_descriptor_chain(name, repeated):
                    continue
                lines = ",".join(str(method.lineno) for method in repeated)
                collisions.append(
                    f"{path.relative_to(ROOT)}:{owner.name}.{name}@{lines}"
                )

    assert collisions == []


def test_private_companion_mro_has_only_the_exact_cooperative_overlap() -> None:
    base_names, resolved = _plugin_base_sources()
    assert set(base_names) - set(resolved) == {"Star"}
    overlaps: dict[tuple[str, str], frozenset[str]] = {}
    for left, right in combinations(base_names, 2):
        if left not in resolved or right not in resolved:
            continue
        shared = frozenset(
            set(_methods(resolved[left][1])) & set(_methods(resolved[right][1]))
        )
        if shared:
            overlaps[(left, right)] = shared

    assert overlaps == {
        (
            "ContentCompanionBridgeMixin",
            "CreativeMixin",
        ): COOPERATIVE_CONTENT_METHODS
    }
    assert base_names.index("ContentCompanionBridgeMixin") + 1 == base_names.index(
        "CreativeMixin"
    )

    bridge_methods = _methods(resolved["ContentCompanionBridgeMixin"][1])
    for method_name in COOPERATIVE_CONTENT_METHODS:
        assert _cooperative_super_targets(bridge_methods[method_name]) == [method_name]


def test_reading_archive_does_not_shadow_self_timeline() -> None:
    base_names, resolved = _plugin_base_sources()
    reading_methods = set(_methods(resolved["ReadingArchiveMixin"][1]))
    timeline_methods = set(_methods(resolved["SelfTimelineMixin"][1]))

    assert reading_methods.isdisjoint(timeline_methods)
    assert "_self_timeline_from_reading_archive" in reading_methods
    assert "_self_timeline_from_reading_archive" not in timeline_methods
    assert "_format_self_timeline_context_for_reply" in timeline_methods
    assert "_format_self_timeline_context_for_reply" not in reading_methods
    assert base_names.index("ReadingArchiveMixin") < base_names.index(
        "SelfTimelineMixin"
    )


def test_retained_photo_reference_facade_forwards_identity_context() -> None:
    owner = _top_level_class(ROOT / "proactive_message.py", "ProactiveMessageMixin")
    definitions = [
        node
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_photo_persona_reference_image_for_kind_async"
    ]
    assert len(definitions) == 1
    method = copy.deepcopy(definitions[0])
    method.decorator_list = []
    keyword_names = {argument.arg for argument in method.args.kwonlyargs}
    assert {"requester_user_id", "continuity_key"} <= keyword_names

    module = ast.Module(
        body=[ast.parse("from __future__ import annotations").body[0], method],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    class _SelectionResult:
        pass

    namespace: dict[str, Any] = {
        "runtime_persona_setting": lambda *_args, **_kwargs: True,
        "SelectionResult": _SelectionResult,
        "_path_text": lambda value, limit=1000: str(value or "")[:limit],
    }
    exec(compile(module, str(ROOT / "proactive_message.py"), "exec"), namespace)

    calls: list[tuple[str, dict[str, Any]]] = []

    async def select(workflow_kind: str, **kwargs: Any) -> dict[str, str]:
        calls.append((workflow_kind, kwargs))
        return {"path": "identity-reference.png"}

    host = types.SimpleNamespace(
        _select_photo_reference_candidate_async=select,
    )
    result = asyncio.run(
        namespace["_photo_persona_reference_image_for_kind_async"](
            host,
            "selfie",
            requester_user_id="owner-id",
            continuity_key="private-window",
            request_text="继续刚才的自拍",
        )
    )

    assert result == "identity-reference.png"
    assert calls == [
        (
            "selfie",
            {
                "allow_daily_outfit": True,
                "requester_user_id": "owner-id",
                "request_text": "继续刚才的自拍",
                "ambient_context": "",
                "selection_context": "",
                "suggested_scene_preset": "",
                "continuity_key": "private-window",
            },
        )
    ]
