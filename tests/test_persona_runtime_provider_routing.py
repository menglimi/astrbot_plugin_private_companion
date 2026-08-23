from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.persona_config import load_scope_manifest
from astrbot_plugin_private_companion.token_budget import TokenBudgetMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PROVIDER_MODULES = (
    "dreaming.py",
    "planning.py",
    "qzone_publish.py",
    "qzone_feed.py",
    "qzone_comments.py",
    "user_memory.py",
    "event_dispatch.py",
    "token_budget.py",
    "llm_tool_actions.py",
    "command_handlers.py",
    "balance_awareness.py",
)


class _PersonaSettingReadVisitor(ast.NodeVisitor):
    def __init__(self, persona_aliases: dict[str, str]) -> None:
        self.persona_aliases = persona_aliases
        self.direct_reads: list[tuple[int, str]] = []

    def _record(self, node: ast.AST, key: str) -> None:
        if key in self.persona_aliases:
            self.direct_reads.append((node.lineno, key))

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {
            "runtime_persona_setting",
            "_persona_value",
        }:
            return
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"}
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in {"self", "plugin"}
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            self._record(node, node.args[1].value)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "plugin"}
        ):
            self._record(node, node.attr)
        self.generic_visit(node)


class _ActivePersonaProviders:
    llm_provider_id = "primary-chat"
    emotion_judgement_provider_id = "primary-emotion"
    troubleshooting_provider_id = "primary-troubleshooting"
    relationship_analysis_provider_id = "primary-relationship"
    mai_style_provider_id = "primary-fast"
    embedding_provider_id = "primary-embedding"
    reaction_expression_embedding_provider_id = "primary-reaction-embedding"

    def __init__(self) -> None:
        self.active_persona = "primary"
        self.persona_values = {
            "secondary": {
                "llm_provider_id": "secondary-chat",
                "emotion_judgement_provider_id": "secondary-emotion",
                "troubleshooting_provider_id": "secondary-troubleshooting",
                "relationship_analysis_provider_id": "secondary-relationship",
                "mai_style_provider_id": "secondary-fast",
                "embedding_provider_id": "secondary-embedding",
                "reaction_expression_embedding_provider_id": "secondary-reaction-embedding",
            }
        }

    def persona_setting(self, key: str, default: Any = None) -> Any:
        if self.active_persona == "primary":
            return getattr(self, key, default)
        return self.persona_values.get(self.active_persona, {}).get(
            key,
            getattr(self, key, default),
        )


class _ChatProviderHarness(TokenBudgetMixin, _ActivePersonaProviders):
    def __init__(self) -> None:
        _ActivePersonaProviders.__init__(self)

    def _default_chat_provider_id(self, umo: str = "") -> str:
        return "astrbot-default"


class _MemoryProviderHarness(UserMemoryMixin, _ActivePersonaProviders):
    def __init__(self) -> None:
        _ActivePersonaProviders.__init__(self)

    @staticmethod
    def _task_provider(*provider_ids: Any) -> str:
        return next((str(value) for value in provider_ids if str(value or "").strip()), "")


class _EmbeddingProviderHarness(LlmToolActionsMixin, _ActivePersonaProviders):
    def __init__(self) -> None:
        _ActivePersonaProviders.__init__(self)

    async def _embedding_provider_for_configured_id(
        self,
        configured_id: Any = "",
    ) -> tuple[Any, str]:
        provider_id = str(configured_id or "")
        return provider_id, provider_id


def test_runtime_provider_modules_do_not_read_persona_settings_directly() -> None:
    manifest = load_scope_manifest()
    persona_aliases = {
        key: key
        for key, entry in manifest.items()
        if entry.get("scope") == "persona"
    }
    persona_aliases.update(
        {
            key.lower(): key
            for key, entry in manifest.items()
            if entry.get("scope") == "persona"
            and key.isupper()
            and key.endswith("_PROVIDER_ID")
        }
    )
    violations: list[str] = []
    for filename in RUNTIME_PROVIDER_MODULES:
        visitor = _PersonaSettingReadVisitor(persona_aliases)
        visitor.visit(ast.parse((ROOT / filename).read_text(encoding="utf-8")))
        violations.extend(
            f"{filename}:{line}:{key}"
            for line, key in visitor.direct_reads
        )
    assert violations == []


def test_primary_and_secondary_personas_route_to_opposite_providers() -> None:
    chat = _ChatProviderHarness()
    memory = _MemoryProviderHarness()
    embedding = _EmbeddingProviderHarness()

    assert chat._resolve_chat_provider_id() == "primary-chat"
    assert memory._emotion_judgement_provider_id() == "primary-emotion"
    assert asyncio.run(embedding._shared_embedding_provider())[1] == "primary-embedding"
    assert (
        asyncio.run(embedding._reaction_embedding_provider())[1]
        == "primary-reaction-embedding"
    )

    chat.active_persona = "secondary"
    memory.active_persona = "secondary"
    embedding.active_persona = "secondary"

    assert chat._resolve_chat_provider_id() == "secondary-chat"
    assert memory._emotion_judgement_provider_id() == "secondary-emotion"
    assert asyncio.run(embedding._shared_embedding_provider())[1] == "secondary-embedding"
    assert (
        asyncio.run(embedding._reaction_embedding_provider())[1]
        == "secondary-reaction-embedding"
    )


def test_runtime_provider_helpers_keep_single_persona_attribute_fallback() -> None:
    chat = _ChatProviderHarness()
    chat.persona_setting = None  # type: ignore[method-assign]
    assert chat._resolve_chat_provider_id() == "primary-chat"

    embedding = _EmbeddingProviderHarness()
    embedding.persona_setting = None  # type: ignore[method-assign]
    assert asyncio.run(embedding._shared_embedding_provider())[1] == "primary-embedding"
