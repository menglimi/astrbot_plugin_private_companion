from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion_interaction_expression import (  # noqa: E402
    EXPRESSION_CONTRACT_VERSION,
    build_expression_decision,
    content_intent_from_text,
)


def _class_method(filename: str, class_name: str, method_name: str, namespace: dict[str, Any]):
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / filename), "exec"), namespace)
    return namespace[method_name]


def _single_line(value: Any, limit: int = 0) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if limit else text


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def test_natural_wake_schedule_wins_over_bedding_and_retrospective_sleep_words() -> None:
    namespace = {"Any": Any, "re": re, "_single_line": _single_line}
    sleepy = _class_method("daily_state.py", "DailyStateMixin", "_is_sleepy_plan_item", namespace)
    host = SimpleNamespace()
    item = {
        "activity": "自然醒后赖在被窝里看看消息",
        "mood": "清醒，慢慢起身",
        "message_seed": "想起昨晚睡前聊到的事",
    }
    assert sleepy(host, item) is False
    assert sleepy(host, {"activity": "醒了一下又继续睡，睡回去补个回笼觉"}) is True
    assert sleepy(host, {"activity": "洗漱后准备早餐", "message_seed": "早上好"}) is False


def test_0824_rest_gate_returns_not_sleeping_for_natural_wake_schedule() -> None:
    namespace = {
        "Any": Any,
        "re": re,
        "_single_line": _single_line,
        "_safe_float": _safe_float,
        "_now_ts": lambda: 1_700_000_000.0,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    sleepy = _class_method("daily_state.py", "DailyStateMixin", "_is_sleepy_plan_item", namespace)
    refresh = _class_method("daily_state.py", "DailyStateMixin", "_refresh_sleep_runtime_state", namespace)
    rest_context = _class_method("main.py", "PrivateCompanionPlugin", "_rest_reply_sleep_context", namespace)
    should_reply = _class_method("main.py", "PrivateCompanionPlugin", "_should_reply_during_rest", namespace)

    class Host:
        enable_rest_reply_simulation = True
        rest_reply_llm_threshold = 65

        def __init__(self) -> None:
            self.item = {
                "activity": "自然醒后还窝在被窝里",
                "mood": "已经清醒",
                "message_seed": "昨晚睡前的话题还记得",
            }
            self.runtime = {"phase": "falling_asleep", "updated_at": 100.0}
            self.data = {"daily_plan": {"items": [self.item]}}

        def _get_current_plan_item(self, _plan: dict[str, Any]) -> dict[str, Any]:
            return self.item

        def _sleep_runtime_state(self) -> dict[str, Any]:
            return self.runtime

        def _sleep_rest_window_active(self) -> bool:
            return True

        def _rest_reply_window_active(self) -> bool:
            return True

        def _sleep_delay_override_state(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def _sleep_awake_grace_seconds(self) -> int:
            return 1800

        def _set_sleep_phase(self, phase: str, **_kwargs: Any) -> dict[str, Any]:
            self.runtime.update({"phase": phase, "updated_at": 1_700_000_000.0})
            return self.runtime

        def _format_plan_item_for_prompt(self, item: dict[str, Any]) -> str:
            return str(item.get("activity") or "")

    Host._is_sleepy_plan_item = sleepy
    Host._refresh_sleep_runtime_state = refresh
    Host._rest_reply_sleep_context = rest_context
    Host._should_reply_during_rest = should_reply
    host = Host()
    sleeping, runtime, _, _ = host._rest_reply_sleep_context()
    assert sleeping is False
    assert runtime["phase"] == "natural_wake"
    allowed, reason = asyncio.run(host._should_reply_during_rest(SimpleNamespace(), is_private_chat=True))
    assert allowed is True
    assert reason == "not_sleeping"


def _content_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
        "relationship_stage": "owner_exclusive",
        "current_interaction": "affectionate",
        "message_intent": {"requested_content_tier": "adult", "turn_consent": True},
        "content_policy": {
            "enabled": True,
            "flirt_enabled": True,
            "adult_enabled": True,
            "adult_owner_confirmed": True,
            "require_turn_consent": True,
            "require_exclusive": True,
            "require_affectionate": True,
            "private_chat": True,
            "local_provider_configured": True,
            "local_provider_match": True,
        },
    }
    for key, value in overrides.items():
        if key.startswith("policy_"):
            payload["content_policy"][key.removeprefix("policy_")] = value
        else:
            payload[key] = value
    return payload


def test_content_tier_matrix_and_intent_are_deterministic() -> None:
    assert EXPRESSION_CONTRACT_VERSION == "companion_interaction_expression.v2"
    assert content_intent_from_text("我同意，开启成人模式并继续") == {
        "requested_content_tier": "adult",
        "turn_consent": True,
    }
    assert content_intent_from_text("请说得暧昧一点")["requested_content_tier"] == "flirt"
    assert build_expression_decision({}).content_tier == "normal"

    flirt = build_expression_decision(
        {
            "relationship_role": "friend",
            "relationship_stage": "intimate",
            "current_interaction": "warm",
            "message_intent": {"requested_content_tier": "flirt"},
            "content_policy": {"enabled": True, "flirt_enabled": True, "private_chat": True},
        }
    )
    assert flirt.content_tier == "flirt"
    assert flirt.content_provider_policy == "current_provider"

    adult = build_expression_decision(_content_payload())
    assert adult.content_tier == "adult"
    assert adult.content_provider_policy == "configured_local_only"


def test_adult_tier_fails_closed_when_any_required_condition_is_missing() -> None:
    cases = {
        "total switch": {"policy_enabled": False},
        "adult switch": {"policy_adult_enabled": False},
        "owner": {"relationship_role": "friend"},
        "exclusive": {"relationship_mode": "normal"},
        "affectionate": {"current_interaction": "warm"},
        "private": {"policy_private_chat": False},
        "age": {"policy_adult_owner_confirmed": False},
        "consent": {"message_intent": {"requested_content_tier": "adult", "turn_consent": False}},
        "provider configured": {"policy_local_provider_configured": False},
        "provider match": {"policy_local_provider_match": False},
    }
    for label, override in cases.items():
        decision = build_expression_decision(_content_payload(**override))
        assert decision.content_tier != "adult", label
        expected_policy = "unmanaged" if label == "total switch" else "current_provider"
        assert decision.content_provider_policy == expected_policy, label


def test_adult_tier_only_relaxes_relationship_expression_gates() -> None:
    relaxed = {
        "affectionate": build_expression_decision(
            _content_payload(current_interaction="warm", policy_require_affectionate=False)
        ),
        "exclusive": build_expression_decision(
            _content_payload(relationship_mode="normal", policy_require_exclusive=False)
        ),
    }
    for label, decision in relaxed.items():
        assert decision.content_tier == "adult", label
        assert decision.content_provider_policy == "configured_local_only", label

    # The global age confirmation only certifies the configured primary user,
    # and a group request cannot establish consent for every audience member.
    owner_boundary = build_expression_decision(
        _content_payload(
            relationship_role="friend",
            policy_require_owner=False,
            policy_require_exclusive=False,
            policy_require_affectionate=False,
        )
    )
    private_boundary = build_expression_decision(
        _content_payload(policy_private_chat=False, policy_require_private_chat=False)
    )
    assert owner_boundary.content_tier != "adult"
    assert "adult_owner_required" in owner_boundary.reason_codes
    assert private_boundary.content_tier != "adult"
    assert "adult_private_required" in private_boundary.reason_codes
    avoidant_boundary = build_expression_decision(
        _content_payload(current_interaction="avoidant", policy_require_affectionate=False)
    )
    assert avoidant_boundary.content_tier != "adult"
    assert "adult_interaction_boundary" in avoidant_boundary.reason_codes


def test_group_proactive_and_unconfigured_paths_remain_normal() -> None:
    group = build_expression_decision(_content_payload(policy_private_chat=False))
    proactive = build_expression_decision(
        {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "current_interaction": "affectionate",
            "message_intent": {"requested_content_tier": "adult", "turn_consent": True},
            "proactive_candidate": {"eligible": True, "daily_allowance": 3},
        }
    )
    assert group.content_tier != "adult"
    assert proactive.content_tier == "normal"


def test_strict_llm_provider_skips_peak_replacement_and_fallback() -> None:
    namespace = {
        "Any": Any,
        "asyncio": asyncio,
        "time": time,
        "_single_line": _single_line,
        "_looks_like_upstream_llm_error_response": lambda _value: False,
        "logger": SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None),
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    llm_call = _class_method("token_budget.py", "TokenBudgetMixin", "_llm_call", namespace)

    class Host:
        llm_provider_id = "cloud-default"
        model_timeout_overrides: dict[str, Any] = {}

        def __init__(self) -> None:
            self.called_provider = ""
            self.peak_calls = 0
            self.fallback_calls = 0
            self.context = SimpleNamespace(llm_generate=self._generate)

        async def _generate(self, **kwargs: Any):
            self.called_provider = kwargs["chat_provider_id"]
            return SimpleNamespace(completion_text="ok")

        def _resolve_chat_provider_id(self, provider_id: str | None) -> str:
            return str(provider_id or self.llm_provider_id)

        def _apply_deepseek_peak_replacement(self, provider_id: str) -> str:
            self.peak_calls += 1
            return "cloud-replacement"

        def _classify_llm_prompt(self, _prompt: str) -> str:
            return "response_review"

        def _is_llm_budget_exempt_task(self, _task: str) -> bool:
            return True

        def _daily_token_soft_limit_should_defer(self, _task: str) -> bool:
            return False

        def _llm_daily_budget_remaining(self) -> int:
            return 1

        def _model_fallback_provider_for_call(self, **_kwargs: Any):
            self.fallback_calls += 1
            return "response_review", "cloud-fallback"

        @staticmethod
        def _model_token_limit_route_for_call(**_kwargs: Any) -> tuple[bool, int | None, int]:
            return False, None, 0

        def _model_timeout_seconds_for_call(self, **_kwargs: Any) -> None:
            return None

        def _record_llm_usage(self, **_kwargs: Any) -> None:
            return None

        def _sensitive_model_replacement_provider(self, _provider_id: str = "") -> str:
            return ""

        @staticmethod
        def _sensitive_model_replacement_keyword(_completion: str) -> str:
            return ""

    Host._llm_call = llm_call
    host = Host()
    result = asyncio.run(
        host._llm_call(
            "review",
            provider_id="local-adult",
            task="response_review",
            strict_provider=True,
        )
    )
    assert result == "ok"
    assert host.called_provider == "local-adult"
    assert host.peak_calls == 0
    assert host.fallback_calls == 0


def test_schema_and_settings_page_expose_fail_closed_content_controls() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    items = schema["basic_config"]["items"]
    assert items["enable_relationship_content_tiers"]["default"] is False
    assert items["enable_adult_content_tier"]["default"] is False
    assert items["adult_content_owner_confirmed"]["default"] is False
    assert items["adult_content_require_turn_consent"]["default"] is True
    assert items["adult_content_require_exclusive"]["default"] is True
    assert items["adult_content_require_affectionate"]["default"] is True
    assert items["ADULT_CONTENT_PROVIDER_ID"]["_special"] == "select_provider"

    source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
    assert 'enable_relationship_content_tiers: ["关系内容尺度"' in source
    assert 'data-feature-open="${escapeHtml(key)}"' in source
    assert 'key === "ADULT_CONTENT_PROVIDER_ID" ? "未配置（成人档不可用）"' in source
    assert "当前会话必须已经使用此 Provider 才能进入成人档" in source
    assert "插件二次复核固定使用它" in source
