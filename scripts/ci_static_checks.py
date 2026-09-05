# -*- coding: utf-8 -*-
"""Dependency-free CI checks for source architecture and packaged imports."""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable


_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        "__pycache__",
        "benchmarks",
        "data",
        "dist",
        "scripts",
        "tests",
        "verification",
        # Legacy standalone checkouts are kept locally for compatibility tests.
        # They are not part of the private companion package or its release
        # archive; the package-level implementation owns their migration.
        "astrbot_plugin_nene_boundary",
        "astrbot_plugin_temp_emotion",
    }
)

_PROMPT_RULE_LEGACY_HEADING = "raw_legacy_heading"
_PROMPT_RULE_CONVERSATION_XML = "raw_conversation_xml"
_PROMPT_RULE_LEGACY_SECTION_CALL = "legacy_prompt_section_call"
_PROMPT_RULE_DIRECT_SECTION_CALL = "direct_prompt_section_constructor"
_PROMPT_RULE_LOOSE_SURFACE_ADD = "loose_prompt_surface_add"
_PROMPT_RULE_LOOSE_PLAN_CALL = "loose_conversation_plan_call"
_PROMPT_RULE_LEGACY_CONTROL_FLAG = "legacy_prompt_control_flag"
_PROMPT_RULE_REMOVED_API = "removed_prompt_api"
_PROMPT_RULE_DIRECT_REQUEST_WRITE = "direct_prompt_request_write"
_PROMPT_RULE_SECTION_IN_CONTENT = "prompt_section_nested_in_content"
_PROMPT_RULE_RAW_MAPPING_CONTENT = "raw_mapping_prompt_content"
_PROMPT_RULE_DELIVERY_BATCH_SECTION = "prompt_delivery_batch_section"
_PROMPT_RULE_DUPLICATE_CHILD_TITLE = "duplicate_prompt_child_title"
_PROMPT_RULE_DUPLICATE_FUNCTION_KEY = "duplicate_prompt_key_in_function"
_PROMPT_RULE_DUPLICATE_FUNCTION_TITLE = "duplicate_prompt_title_in_function"
_LEGACY_HEADING_PATTERN = re.compile(r"【[^】\n]{1,100}】")
_CONVERSATION_XML_PATTERN = re.compile(
    r"<private_companion_context\b|<section(?:\s|/?>)|<!\[CDATA\[",
    flags=re.I,
)
_REMOVED_PROMPT_SYMBOLS = frozenset(
    {
        "PhotoPromptSection",
        "PromptValue",
        "coerce_prompt_section",
        "legacy_heading_token",
        "prompt_value",
        "render_prompt_section",
        "render_partition_with_fragments",
        "rendered_fragments",
        "rendered_sections",
    }
)
_REMOVED_PROMPT_METADATA = frozenset(
    {
        "legacy_heading_style",
        "legacy_prefix",
        "legacy_render_mode",
        "legacy_separator_before",
    }
)

def _prompt_allow(
    rule: str,
    path: str,
    owner: str,
    tokens: tuple[str, ...],
    reason: str,
    remove_when: str,
) -> dict[str, object]:
    return {
        "rule": rule,
        "path": path,
        "owner": owner,
        "tokens": tokens,
        "reason": reason,
        "remove_when": remove_when,
    }


# Each exception is deliberately narrower than a file. ``tokens`` lists the
# exact legacy syntax already owned by that function; new syntax in the same
# function remains a CI failure. ``remove_when`` keeps compatibility debt
# visible instead of turning the allowlist into a permanent escape hatch.
_PROMPT_AUTHORING_ALLOWLIST: tuple[dict[str, object], ...] = (
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "conversation_prompt_section.py",
        "_plain_content",
        ("【{value}】",),
        "canonical typed heading-reference renderer",
        "labeled background wire formats are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "conversation_prompt_section.py",
        "_render_labeled_section",
        ("【{value}】",),
        "canonical LABELED_BLOCK and LABELED_INLINE renderer",
        "labeled background wire formats are retired",
    ),
    *(
        _prompt_allow(
            _PROMPT_RULE_DIRECT_SECTION_CALL,
            "conversation_prompt_section.py",
            owner,
            ("PromptSection constructor",),
            "canonical factory, coercion or renderer implementation",
            "PromptSection construction is made private to prompt_section",
        )
        for owner in (
            "PromptSection.__deepcopy__",
            "prompt_section",
        )
    ),
    _prompt_allow(
        _PROMPT_RULE_DIRECT_REQUEST_WRITE,
        "conversation_injection_plan.py",
        "ConversationInjectionPlan._render_system",
        ("req.system_prompt",),
        "canonical request-scoped system prompt renderer",
        "the host provides a typed prompt placement API",
    ),
    _prompt_allow(
        _PROMPT_RULE_DIRECT_REQUEST_WRITE,
        "conversation_injection_plan.py",
        "ConversationInjectionPlan.render_into",
        ("req.prompt",),
        "canonical request-scoped turn prompt renderer",
        "the host provides a typed prompt placement API",
    ),
    *(
        _prompt_allow(
            _PROMPT_RULE_CONVERSATION_XML,
            "conversation_prompt_section.py",
            owner,
            ("<![CDATA[",),
            "canonical CDATA renderer",
            "conversation XML wire format is retired",
        )
        for owner in ("_cdata_text", "_render_xml_value", "_render_xml_child", "_render_xml_content")
    ),
    _prompt_allow(
        _PROMPT_RULE_CONVERSATION_XML,
        "conversation_prompt_section.py",
        "_render_conversation_xml",
        ("<private_companion_context",),
        "canonical conversation XML root renderer",
        "conversation XML wire format is retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_CONVERSATION_XML,
        "conversation_prompt_section.py",
        "_render_xml_section",
        ("<section ",),
        "canonical conversation XML section renderer",
        "conversation XML wire format is retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "daily_state.py",
        "DailyStateMixin._daily_proactive_archive_context_text",
        ("【主动消息】",),
        "recognizes legacy persisted proactive archive rows",
        "legacy archive rows have expired or migrated",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "event_dispatch.py",
        "EventDispatchMixin._is_legacy_proactive_prompt_trace",
        ("【怎么写这条消息】", "【禁止事项】", "【状态表现层】", "【主动意图具体化】", "【语言风格疲劳】"),
        "detects persisted legacy proactive prompt traces",
        "legacy trace retention window has elapsed",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "event_dispatch.py",
        "EventDispatchMixin._prompt_injection_preview_is_internal_prompt",
        ("【语音消息规则】",),
        "filters internal prompt previews from diagnostics",
        "legacy TTS previews are no longer persisted",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "event_dispatch.py",
        "EventDispatchMixin._is_duplicate_inbound_message",
        ("【图片】",),
        "recognizes the legacy inbound image placeholder",
        "legacy media placeholders are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "group_observation.py",
        "GroupObservationMixin._group_slang_candidates_from_text",
        ("【]?([\\u4e00-\\u9fffA-Za-z0-9_]{2,12})[”\\\"」』】",),
        "regular expressions recognize quoted user slang",
        "legacy bracket quoting is no longer parsed",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "group_observation.py",
        "GroupObservationMixin._group_repeat_signature",
        ("【图片】", "【语音】", "【视频】", "【文件】"),
        "normalizes legacy media placeholders for repeat detection",
        "legacy media placeholders are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "main.py",
        "PrivateCompanionPlugin._schedule_reply_interception_forward",
        ("【回复拦截转发】",),
        "user-visible forwarded message label, not an LLM prompt heading",
        "forwarded control labels use structured message components",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "main.py",
        "PrivateCompanionPlugin.companion_command",
        ("【图片】",),
        "recognizes a legacy image-only command result",
        "legacy media placeholders are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "page_api.py",
        "PrivateCompanionPageApi._prompt_injection_summary.preview_is_internal_prompt",
        ("【语音消息规则】",),
        "diagnostic preview filter for old TTS prompt traces",
        "legacy prompt traces are no longer retained",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "planning.py",
        "split_detail_prompt_cache_sections",
        ("【A｜当前段硬框架】",),
        "parses the persisted legacy detail prompt cache prefix",
        "detail prompt cache is keyed by section metadata",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "private_image.py",
        "PrivateImageMixin._is_private_image_only_message",
        ("【图片】",),
        "recognizes a legacy image-only message placeholder",
        "legacy media placeholders are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "private_image.py",
        "PrivateImageMixin._context_image_skip_text",
        ("【本轮延迟图片】", "【本轮引用图片】", "【当前引用图片锚点】", "【本轮合并消息】", "【本轮合并消息转述】"),
        "detects already-materialized legacy image context in compatibility input",
        "all image context consumers use section keys",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "private_image.py",
        "PrivateImageMixin._enrich_request_context_image_placeholders",
        ("【图片摘要：{value}】",),
        "message placeholder shown to the host context, not a prompt section title",
        "host image summaries use structured message metadata",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "proactive_message.py",
        "ProactiveMessageMixin._remove_unbacked_media_claims",
        ("【图片】",),
        "sanitizes legacy image placeholders from generated text",
        "legacy media placeholders are retired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "proactive_message.py",
        "ProactiveMessageMixin._proactive_archive_context_text",
        ("【主动承接占位】",),
        "recognizes a persisted proactive archive placeholder",
        "legacy proactive archive rows have expired",
    ),
    _prompt_allow(
        _PROMPT_RULE_LEGACY_HEADING,
        "proactive_message.py",
        "ProactiveMessageMixin._sanitize_proactive_text",
        ("【图片】",),
        "sanitizes a legacy media placeholder from generated text",
        "legacy media placeholders are retired",
    ),
)


def _sources(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts)
        )
    )


def _module_name(root: Path, path: Path) -> tuple[str, bool]:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _local_target(candidate: str, modules: set[str]) -> str | None:
    value = candidate
    while value:
        if value in modules:
            return value
        value = value.rpartition(".")[0]
    return None


def _imports(
    tree: ast.Module,
    *,
    module: str,
    is_package: bool,
    modules: set[str],
) -> set[str]:
    result: set[str] = set()
    current = module.split(".") if module else []
    package = current if is_package else current[:-1]
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _local_target(alias.name, modules)
                if target:
                    result.add(target)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            trim = node.level - 1
            base = package[: max(0, len(package) - trim)]
            if node.module:
                candidate = ".".join((*base, *node.module.split(".")))
                target = _local_target(candidate, modules)
                if target:
                    result.add(target)
            else:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    target = _local_target(".".join((*base, alias.name)), modules)
                    if target:
                        result.add(target)
        elif node.module:
            target = _local_target(node.module, modules)
            if target:
                result.add(target)
    result.discard(module)
    return result


def _property_extension(node: ast.AST, name: str) -> bool:
    decorators = getattr(node, "decorator_list", ())
    return any(
        isinstance(decorator, ast.Attribute)
        and decorator.attr in {"setter", "deleter"}
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == name
        for decorator in decorators
    )


def _duplicate_findings(path: Path, tree: ast.Module) -> list[str]:
    findings: list[str] = []
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    counts = Counter(node.name for node in definitions)
    for name, count in sorted(counts.items()):
        if count > 1:
            findings.append(f"{path}: duplicate top-level symbol {name!r}")
    for class_node in (node for node in definitions if isinstance(node, ast.ClassDef)):
        bases = [ast.unparse(base) for base in class_node.bases]
        for base, count in Counter(bases).items():
            if count > 1:
                findings.append(
                    f"{path}:{class_node.lineno}: duplicate base {base!r} in {class_node.name}"
                )
        members = [
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        by_name: dict[str, list[ast.AST]] = {}
        for member in members:
            by_name.setdefault(member.name, []).append(member)
        for name, duplicates in sorted(by_name.items()):
            if len(duplicates) <= 1:
                continue
            if all(_property_extension(node, name) for node in duplicates[1:]):
                continue
            findings.append(
                f"{path}:{duplicates[1].lineno}: duplicate class member {class_node.name}.{name}"
            )
    return findings


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    result: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            result.add(id(first.value))
    return result


def _joined_string_text(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{value}")
    return "".join(parts)


def _composed_string_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _joined_string_text(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _composed_string_text(node.left)
        right = _composed_string_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


class _DirectPromptSectionVisitor(ast.NodeVisitor):
    """Collect prompt_section calls without crossing nested function scopes."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        function_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if function_name == "prompt_section":
            self.calls.append(node)
        self.generic_visit(node)


def _direct_prompt_section_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    visitor = _DirectPromptSectionVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.calls


def _allowlist_matches(
    *,
    rule: str,
    path: str,
    owner: str,
    token: str,
) -> int | None:
    for index, entry in enumerate(_PROMPT_AUTHORING_ALLOWLIST):
        if (
            entry.get("rule") != rule
            or entry.get("path") != path
            or entry.get("owner") != owner
        ):
            continue
        tokens = entry.get("tokens")
        if isinstance(tokens, tuple) and token in tokens:
            return index
    return None


class _PromptAuthoringVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        tree: ast.Module,
        matched_allowlist: set[tuple[int, str]] | None = None,
    ) -> None:
        self.path = path
        self.docstrings = _docstring_node_ids(tree)
        self.scope: list[str] = []
        self.findings: list[str] = []
        self.matched_allowlist = matched_allowlist if matched_allowlist is not None else set()

    @property
    def owner(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _record(self, *, rule: str, node: ast.AST, token: str) -> None:
        allowed_index = _allowlist_matches(
            rule=rule,
            path=self.path,
            owner=self.owner,
            token=token,
        )
        if allowed_index is not None:
            self.matched_allowlist.add((allowed_index, token))
            return
        self.findings.append(
            f"{self.path}:{getattr(node, 'lineno', 0)}: {rule} "
            f"in {self.owner}: {token!r}"
        )

    def _check_text(self, node: ast.AST, text: str) -> None:
        if text in _REMOVED_PROMPT_METADATA:
            self._record(
                rule=_PROMPT_RULE_REMOVED_API,
                node=node,
                token=text,
            )
        for match in _LEGACY_HEADING_PATTERN.finditer(text):
            self._record(
                rule=_PROMPT_RULE_LEGACY_HEADING,
                node=node,
                token=match.group(0),
            )
        for match in _CONVERSATION_XML_PATTERN.finditer(text):
            token = match.group(0)
            self._record(
                rule=_PROMPT_RULE_CONVERSATION_XML,
                node=node,
                token=token,
            )

    def _visit_scope(self, node: ast.AST) -> None:
        self.scope.append(getattr(node, "name"))
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function_scope(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self.scope.append(node.name)
        if node.name in _REMOVED_PROMPT_SYMBOLS:
            self._record(
                rule=_PROMPT_RULE_REMOVED_API,
                node=node,
                token=node.name,
            )
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            if argument.arg in {"include_heading", "as_section", "as_sections"}:
                self._record(
                    rule=_PROMPT_RULE_LEGACY_CONTROL_FLAG,
                    node=argument,
                    token=argument.arg,
                )
        identities: dict[tuple[str, str], ast.Call] = {}
        for call in _direct_prompt_section_calls(node):
            keywords = {item.arg: item.value for item in call.keywords if item.arg}
            for field, rule in (
                ("key", _PROMPT_RULE_DUPLICATE_FUNCTION_KEY),
                ("title", _PROMPT_RULE_DUPLICATE_FUNCTION_TITLE),
            ):
                value_node = keywords.get(field)
                value = _composed_string_text(value_node) if value_node is not None else None
                if not value:
                    continue
                identity = (field, value)
                if identity in identities:
                    self._record(
                        rule=rule,
                        node=value_node,
                        token=value,
                    )
                else:
                    identities[identity] = call
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function_scope(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if id(node) not in self.docstrings and isinstance(node.value, str):
            self._check_text(node, node.value)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802
        self._check_text(node, _joined_string_text(node))
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                self.visit(value.value)

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
        composed = _composed_string_text(node)
        if composed is not None:
            self._check_text(node, composed)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name in _REMOVED_PROMPT_SYMBOLS:
                self._record(
                    rule=_PROMPT_RULE_REMOVED_API,
                    node=node,
                    token=alias.name,
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in {"LEGACY_BLOCK", "LEGACY_INLINE"}:
            self._record(
                rule=_PROMPT_RULE_REMOVED_API,
                node=node,
                token=node.attr,
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if function_name in _REMOVED_PROMPT_SYMBOLS:
            self._record(
                rule=_PROMPT_RULE_REMOVED_API,
                node=node,
                token=function_name,
            )
        if function_name == "prompt_section":
            keyword_names = {item.arg for item in node.keywords if item.arg}
            if node.args or not {"key", "title", "source"}.issubset(keyword_names):
                self._record(
                    rule=_PROMPT_RULE_LEGACY_SECTION_CALL,
                    node=node,
                    token="positional arguments" if node.args else "missing key/title/source",
                )
            content_keyword = next(
                (item for item in node.keywords if item.arg == "content"),
                None,
            )
            if content_keyword is not None and isinstance(content_keyword.value, ast.Dict):
                self._record(
                    rule=_PROMPT_RULE_RAW_MAPPING_CONTENT,
                    node=content_keyword.value,
                    token="content={...}",
                )
            key_keyword = next(
                (item for item in node.keywords if item.arg == "key"),
                None,
            )
            key_text = (
                _composed_string_text(key_keyword.value)
                if key_keyword is not None
                else None
            )
            if key_text and (
                key_text.endswith(".batch")
                or key_text in {"passive.static", "passive.dynamic"}
            ):
                self._record(
                    rule=_PROMPT_RULE_DELIVERY_BATCH_SECTION,
                    node=key_keyword.value,
                    token=key_text,
                )
            title_keyword = next(
                (item for item in node.keywords if item.arg == "title"),
                None,
            )
            children_keyword = next(
                (item for item in node.keywords if item.arg == "children"),
                None,
            )
            parent_title = (
                _composed_string_text(title_keyword.value)
                if title_keyword is not None
                else None
            )
            if parent_title and children_keyword is not None:
                children = (
                    children_keyword.value.elts
                    if isinstance(children_keyword.value, (ast.List, ast.Tuple))
                    else ()
                )
                for child in children:
                    if not isinstance(child, ast.Call):
                        continue
                    child_name = (
                        child.func.id
                        if isinstance(child.func, ast.Name)
                        else child.func.attr
                        if isinstance(child.func, ast.Attribute)
                        else ""
                    )
                    if child_name != "prompt_section":
                        continue
                    child_title_keyword = next(
                        (item for item in child.keywords if item.arg == "title"),
                        None,
                    )
                    child_title = (
                        _composed_string_text(child_title_keyword.value)
                        if child_title_keyword is not None
                        else None
                    )
                    if child_title == parent_title:
                        self._record(
                            rule=_PROMPT_RULE_DUPLICATE_CHILD_TITLE,
                            node=child_title_keyword.value,
                            token=parent_title,
                        )
        elif function_name == "PromptSection":
            self._record(
                rule=_PROMPT_RULE_DIRECT_SECTION_CALL,
                node=node,
                token="PromptSection constructor",
            )
        elif function_name in {
            "prompt_cdata",
            "prompt_field",
            "prompt_group",
            "prompt_list",
            "prompt_text",
            "xml_element",
        }:
            nested_section = next(
                (
                    child
                    for child in ast.walk(node)
                    if child is not node
                    and isinstance(child, ast.Call)
                    and (
                        (isinstance(child.func, ast.Name) and child.func.id == "prompt_section")
                        or (
                            isinstance(child.func, ast.Attribute)
                            and child.func.attr == "prompt_section"
                        )
                    )
                ),
                None,
            )
            if nested_section is not None:
                self._record(
                    rule=_PROMPT_RULE_SECTION_IN_CONTENT,
                    node=node,
                    token=function_name,
                )
        if function_name == "setattr" and len(node.args) >= 2:
            target, field = node.args[:2]
            if (
                isinstance(target, ast.Name)
                and target.id in {"req", "request"}
                and isinstance(field, ast.Constant)
                and field.value in {"prompt", "system_prompt"}
            ):
                self._record(
                    rule=_PROMPT_RULE_DIRECT_REQUEST_WRITE,
                    node=node,
                    token=f"{target.id}.{field.value}",
                )
        if isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            keyword_names = {item.arg for item in node.keywords if item.arg}
            loose_fields = keyword_names & {
                "key",
                "content",
                "title",
                "source",
                "structured",
                "opaque",
            }
            receiver_name = receiver.rsplit(".", 1)[-1]
            surface_call = node.func.attr == "add" and (
                receiver_name in {"prompt_surface", "surface"}
                or (receiver == "self" and self.owner.startswith("PromptSurface."))
            )
            plan_call = (
                node.func.attr in {"add", "materialize_system_block"}
                and (
                    node.func.attr == "materialize_system_block"
                    or receiver_name in {"plan", "conversation_plan", "request_plan", "injection_plan"}
                    or (receiver == "self" and self.owner.startswith("ConversationInjectionPlan."))
                )
            )
            if surface_call and (len(node.args) > 1 or loose_fields & {"key", "content", "title", "source"}):
                token = (
                    ",".join(sorted(loose_fields & {"key", "content", "title", "source"}))
                    or "multiple positional arguments"
                )
                self._record(
                    rule=_PROMPT_RULE_LOOSE_SURFACE_ADD,
                    node=node,
                    token=token,
                )
            if plan_call and loose_fields:
                self._record(
                    rule=_PROMPT_RULE_LOOSE_PLAN_CALL,
                    node=node,
                    token=",".join(sorted(loose_fields)),
                )
        if isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            if node.func.attr in {
                "debug",
                "info",
                "warning",
                "error",
                "exception",
                "critical",
                "log",
            } and (receiver == "logging" or receiver.endswith("logger") or receiver == "logger"):
                return
            if receiver == "re" and node.func.attr in {
                "compile",
                "search",
                "match",
                "fullmatch",
                "findall",
                "finditer",
                "split",
                "sub",
                "subn",
            }:
                for argument in node.args[1:]:
                    self.visit(argument)
                for keyword in node.keywords:
                    if keyword.arg not in {"pattern", "flags"}:
                        self.visit(keyword.value)
                return
        self.generic_visit(node)

    def _check_request_write_target(self, node: ast.AST, target: ast.AST) -> None:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"req", "request"}
            and target.attr in {"prompt", "system_prompt"}
        ):
            self._record(
                rule=_PROMPT_RULE_DIRECT_REQUEST_WRITE,
                node=node,
                token=f"{target.value.id}.{target.attr}",
            )

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            self._check_request_write_target(node, target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self._check_request_write_target(node, node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self._check_request_write_target(node, node.target)
        self.generic_visit(node)


def _validate_prompt_allowlist() -> list[str]:
    findings: list[str] = []
    required = {"rule", "path", "owner", "tokens", "reason", "remove_when"}
    seen: set[tuple[object, object, object, object]] = set()
    for index, entry in enumerate(_PROMPT_AUTHORING_ALLOWLIST):
        missing = required - set(entry)
        if missing:
            findings.append(f"prompt allowlist entry {index} missing: {sorted(missing)}")
            continue
        tokens = entry.get("tokens")
        if not isinstance(tokens, tuple) or not tokens or not all(
            isinstance(item, str) and item for item in tokens
        ):
            findings.append(f"prompt allowlist entry {index} has invalid tokens")
            continue
        for token in tokens:
            identity = (entry.get("rule"), entry.get("path"), entry.get("owner"), token)
            if identity in seen:
                findings.append(f"duplicate prompt allowlist entry: {identity!r}")
            seen.add(identity)
        for field in ("reason", "remove_when"):
            if not isinstance(entry.get(field), str) or not str(entry.get(field)).strip():
                findings.append(f"prompt allowlist entry {index} has empty {field}")
    return findings


def check_prompt_authoring(
    root: Path,
    *,
    enforce_allowlist_usage: bool = True,
) -> None:
    """Reject prompt syntax that bypasses the canonical authoring model."""

    findings = _validate_prompt_allowlist()
    matched_allowlist: set[tuple[int, str]] = set()
    for path in _sources(root):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append(f"{path}: cannot parse for prompt authoring: {exc}")
            continue
        visitor = _PromptAuthoringVisitor(
            path=relative,
            tree=tree,
            matched_allowlist=matched_allowlist,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)
    if enforce_allowlist_usage:
        for index, entry in enumerate(_PROMPT_AUTHORING_ALLOWLIST):
            for token in entry.get("tokens", ()):
                if (index, token) in matched_allowlist:
                    continue
                findings.append(
                    "stale_prompt_allowlist: "
                    f"{entry.get('path')}:{entry.get('owner')} "
                    f"{entry.get('rule')} token={token!r}"
                )
    if findings:
        raise SystemExit("\n".join(findings))


def _cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    visiting: list[str] = []
    active: set[str] = set()
    complete: set[str] = set()

    def canonical(cycle: list[str]) -> tuple[str, ...]:
        body = cycle[:-1]
        rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
        return min(rotations)

    def visit(node: str) -> None:
        if node in complete:
            return
        if node in active:
            index = visiting.index(node)
            found.add(canonical(visiting[index:] + [node]))
            return
        active.add(node)
        visiting.append(node)
        for dependency in sorted(graph.get(node, ())):
            visit(dependency)
        visiting.pop()
        active.remove(node)
        complete.add(node)

    for name in sorted(graph):
        visit(name)
    return sorted(found)


def check_architecture(root: Path) -> None:
    sources = _sources(root)
    module_rows = [(path, *_module_name(root, path)) for path in sources]
    modules = {module for _path, module, _is_package in module_rows if module}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    findings: list[str] = []
    for path, module, is_package in module_rows:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append(f"{path}: cannot parse: {exc}")
            continue
        findings.extend(_duplicate_findings(path, tree))
        if module:
            graph[module] = _imports(
                tree,
                module=module,
                is_package=is_package,
                modules=modules,
            )
    for cycle in _cycles(graph):
        findings.append("import cycle: " + " -> ".join((*cycle, cycle[0])))
    if findings:
        raise SystemExit("\n".join(findings))


def check_artifact_import(root: Path, package: str, module: str) -> None:
    with tempfile.TemporaryDirectory(prefix="plugin-artifact-") as temporary:
        archive = Path(temporary) / "plugin.zip"
        extracted = Path(temporary) / "extracted"
        files = tuple(
            sorted(
                path
                for path in root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and not any(
                    part in _EXCLUDED_DIRS
                    for part in path.relative_to(root).parts
                )
            )
        )
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            has_init = False
            for path in files:
                relative = path.relative_to(root)
                if relative == Path("__init__.py"):
                    has_init = True
                bundle.write(path, f"{package}/{relative.as_posix()}")
            if not has_init:
                bundle.writestr(f"{package}/__init__.py", "# artifact package\n")
        with zipfile.ZipFile(archive, "r") as bundle:
            bundle.extractall(extracted)
        statement = (
            "import importlib,sys;"
            f"sys.path.insert(0,{str(extracted)!r});"
            f"importlib.import_module({f'{package}.{module}'!r})"
        )
        subprocess.run(
            [sys.executable, "-I", "-c", statement],
            check=True,
            cwd=temporary,
        )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--import-module", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    check_architecture(root)
    check_prompt_authoring(root)
    check_artifact_import(root, args.package, args.import_module)
    print("architecture and artifact checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
