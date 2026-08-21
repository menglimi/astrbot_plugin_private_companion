from __future__ import annotations

import ast
from pathlib import Path

from astrbot_plugin_private_companion.astrbot_knowledge import AstrBotKnowledgeMixin
from astrbot_plugin_private_companion.busy_reply_gate import BusyReplyGateMixin
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.group_member_safety import GroupMemberSafetyMixin
from astrbot_plugin_private_companion.nai_image_bridge import NAIImageBridgeMixin
from astrbot_plugin_private_companion.persona_config import load_scope_manifest


ROOT = Path(__file__).resolve().parents[1]
UTILITY_MODULES = (
    "core_store.py",
    "forward_message.py",
    "worldbook.py",
    "group_member_safety.py",
    "busy_reply_gate.py",
    "passive_state_pipeline.py",
    "astrbot_knowledge.py",
    "atrelay.py",
    "interaction_utils.py",
    "final_response_persistence.py",
    "tts_tool_sanitizer.py",
    "scene_context.py",
    "nai_image_bridge.py",
)
PROVIDER_ATTR_ALIASES = {
    "llm_provider_id": "LLM_PROVIDER_ID",
    "mai_style_provider_id": "MAI_STYLE_PROVIDER_ID",
    "relationship_analysis_provider_id": "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
    "forward_message_provider_id": "FORWARD_MESSAGE_PROVIDER_ID",
    "group_member_safety_provider_id": "GROUP_MEMBER_SAFETY_PROVIDER_ID",
    "group_followup_judge_provider_id": "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
    "response_review_provider_id": "RESPONSE_REVIEW_PROVIDER_ID",
}


class _PersonaReadVisitor(ast.NodeVisitor):
    def __init__(self, persona_keys: set[str]) -> None:
        self.persona_keys = persona_keys
        self.direct_reads: list[tuple[int, str]] = []

    def _is_persona_key(self, key: str) -> bool:
        return PROVIDER_ATTR_ALIASES.get(key, key) in self.persona_keys

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "runtime_persona_setting":
            return
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in {"self", "plugin"}
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and self._is_persona_key(node.args[1].value)
        ):
            self.direct_reads.append((node.lineno, node.args[1].value))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "plugin"}
            and self._is_persona_key(node.attr)
        ):
            self.direct_reads.append((node.lineno, node.attr))
        self.generic_visit(node)


class _PersonaHarness(
    AstrBotKnowledgeMixin,
    BusyReplyGateMixin,
    CoreStoreMixin,
    GroupMemberSafetyMixin,
    NAIImageBridgeMixin,
):
    def __init__(self) -> None:
        self._overrides: dict[str, object] = {}
        self.data: dict[str, object] = {}
        self.group_whitelist_ids = ["primary-group"]
        self.target_group_ids = []
        self.photo_generation_backend = "auto"

    def persona_setting(self, key: str, default: object = None, persona_id: str = "") -> object:
        return self._overrides.get(key, getattr(self, key, default))


def test_utility_modules_do_not_read_manifest_persona_settings_directly() -> None:
    manifest = load_scope_manifest()
    persona_keys = {key for key, entry in manifest.items() if entry.get("scope") == "persona"}
    failures: list[str] = []
    for filename in UTILITY_MODULES:
        visitor = _PersonaReadVisitor(persona_keys)
        visitor.visit(ast.parse((ROOT / filename).read_text(encoding="utf-8")))
        failures.extend(f"{filename}:{line}:{key}" for line, key in visitor.direct_reads)
    assert failures == []


def test_utility_runtime_reads_persona_overrides_without_mutating_primary_attrs() -> None:
    harness = _PersonaHarness()
    harness._overrides.update(
        {
            "group_whitelist_ids": ["persona-group"],
            "target_group_ids": [],
            "enable_group_member_safety": False,
            "group_member_safety_review_mode": "all",
            "photo_generation_backend": "nai",
            "bot_name": "次人格",
            "schedule_persona_prompt": "次人格设定",
        }
    )

    assert harness._configured_group_ids() == ["persona-group"]
    assert harness.group_whitelist_ids == ["primary-group"]
    safety = harness._group_member_safety_compact_summary({})
    assert safety["enabled"] is False
    assert safety["review_mode"] == "all"
    assert harness._nai_image_selected() is True
    query = harness._build_roleplay_knowledge_query(purpose="schedule")
    assert "次人格" in query
    assert "次人格设定" in query


def test_lightweight_harness_without_persona_accessor_keeps_attribute_fallback() -> None:
    harness = _PersonaHarness()
    del harness._overrides
    harness.persona_setting = None  # type: ignore[method-assign]

    assert harness._configured_group_ids() == ["primary-group"]
    assert harness._nai_image_selected() is False
