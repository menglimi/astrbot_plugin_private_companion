"""Public DecisionService contracts with synthetic providers and no network."""

import asyncio
from dataclasses import dataclass, replace
import json
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from astrbot_host import decision_provider as adapter
from astrbot_host.laya_client import LayaDecisionClient, laya_client_factory
from companion_v2.decision.client import LayaClientError
from companion_v2.decision.prompt import DECISION_QUESTIONS
from companion_v2.decision.service import LayaDecisionService
from companion_v2.decision.store import LayaSettingsStore
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.replacement_rules import checked_rules, validate_targets
from tests.test_model_gateway import ModelFixture
from tests.test_proactive_appointments_and_judge import JudgeFixture


@dataclass(frozen=True)
class ChoiceQuestion:
    instructions: str
    criteria: dict


@dataclass(frozen=True)
class ScoreQuestion:
    instructions: str
    criteria: list


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probability: float
    probabilities: dict
    confidence: float


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    legend: dict
    probabilities: dict
    confidence: float


TYPES = (ChoiceQuestion, ScoreQuestion, ChoiceAnswer, ScoreAnswer)


class Native:
    provider_config = {"id": "decision/test", "model": "test-model"}

    def meta(self):
        return SimpleNamespace(id="decision/test", model="test-model", provider_type="decision")

    async def ask(self, questions, state=None):
        raise AssertionError("Use the public DecisionService, not a native transport")


class Host:
    def __init__(self):
        self.native = Native()
        self.provider_manager = SimpleNamespace(decision_provider_insts=[self.native])
        self.decision = SimpleNamespace(ask=self.ask)
        self.calls = []
        self.fail = None
        self.decision_value, self.reason, self.level = "send", "appropriate", 4

    def get_provider_by_id(self, identifier):
        return self.native if identifier == "decision/test" else None

    async def ask(self, questions, state=None, *, provider_id=None):
        self.calls.append((questions, state, provider_id))
        if self.fail:
            raise self.fail
        results = {}
        for key, question in questions.items():
            if isinstance(question, ScoreQuestion):
                legend = {str(i): text for i, text in enumerate(question.criteria)}
                probabilities = {i: float(int(i) == self.level) for i in legend}
                results[key] = ScoreAnswer(float(self.level), legend, probabilities, 0.99)
            else:
                chosen = {"decision": self.decision_value, "reason": self.reason}.get(key, next(iter(question.criteria)))
                distribution = {name: (0.7 if name == chosen else 0.3 / (len(question.criteria) - 1))
                                for name in question.criteria}
                results[key] = ChoiceAnswer(chosen, 0.7, distribution, 0.99)
        return results


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(adapter, "_types", lambda: TYPES)
    return Host()


def test_catalog_excludes_chat_models_and_has_no_credentials(host):
    host.native.provider_config = {**host.native.provider_config, "key": "synthetic-secret"}
    assert adapter.decision_catalog(host) == [{"id": "decision/test", "name": "decision/test", "model": "test-model"}]
    host.provider_manager.decision_provider_insts.append(SimpleNamespace(meta=lambda: SimpleNamespace(
        id="chat", model="chat", provider_type="chat_completion"), ask=lambda: None))
    assert len(adapter.assemble_decision_providers(host)) == 1
    assert adapter.decision_catalog(SimpleNamespace()) == []


def test_host_factory_joins_the_existing_provider_catalog_without_sdk_import(host):
    from companion_v2.adapters.astrbot.provider_catalog import assemble_providers
    from tests.test_provider_catalog_diagnostics import NATIVE_TYPES
    host.get_all_providers = lambda: []
    native_types = SimpleNamespace(**vars(NATIVE_TYPES), decision_provider_factory=adapter.assemble_decision_providers)
    models = assemble_providers(host, bridge=None, native_types=native_types, primary_id="text-primary")
    assert [model.provider_id for model in models] == ["decision/test"]
    assert models[0].local_provider().task_kinds == ("proactive.judge",)
    assert host.calls == []
    host.get_provider_by_id = lambda _identifier: None
    assert adapter.assemble_decision_providers(host) == ()


def test_laya_official_mode_calls_host_once_and_preserves_selected_probability(host):
    client = LayaDecisionClient(mode="astrbot", context=host, astrbot_provider_id="decision/test")
    result = asyncio.run(client.probe(enabled_fields={key: key == "action" for key in DECISION_QUESTIONS}))
    assert len(host.calls) == 1
    assert set(host.calls[0][0]) == {"action"}
    assert host.calls[0][2] == "decision/test"
    assert result.action_confidence == 0.7  # Not the unrelated entropy confidence .99.
    assert client.endpoint == ""
    assert client.last_trace["response"]["value"]["usage_availability"] == "unavailable"


def test_laya_official_mode_uses_default_and_existing_intervention_contract(host):
    client = LayaDecisionClient(mode="astrbot", context=host)
    result = asyncio.run(client.classify_intervention({"scope_kind": "private", "user_text": "a synthetic request",
        "allowed_domains": ["state", "calendar"]}))
    assert result in {"none", "state", "calendar", "both"}
    assert host.calls[0][2] is None


@pytest.mark.parametrize("failure", [RuntimeError("synthetic-secret"), ValueError("synthetic-secret")])
def test_failed_host_call_has_fixed_error_and_no_secret(host, failure):
    host.fail = failure
    with pytest.raises(LayaClientError) as error:
        asyncio.run(LayaDecisionClient(mode="astrbot", context=host).probe())
    assert error.value.code == "astrbot_decision_failed"
    assert "synthetic-secret" not in str(error.value)
    assert len(host.calls) == 1


def test_official_timeout_does_not_retry(host):
    async def slow(*args, **kwargs):
        host.calls.append("started")
        await asyncio.sleep(10)
    host.decision.ask = slow
    client = LayaDecisionClient(mode="astrbot", context=host, timeout_seconds=0.01)
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.probe())
    assert error.value.code == "laya_inference_timeout"
    assert host.calls == ["started"]


def test_old_host_has_an_explicit_unavailable_result(host):
    host.decision = None
    assert adapter.decision_status(host) == {"astrbot_available": False, "astrbot_providers": []}
    with pytest.raises(LayaClientError) as error:
        asyncio.run(LayaDecisionClient(mode="astrbot", context=host).probe())
    assert error.value.code == "astrbot_decision_unavailable"


@pytest.mark.parametrize("kind", ["score", "legend", "distribution"])
def test_invalid_score_never_becomes_a_review(host, kind):
    from companion_v2.decision.proactive import questions
    original = host.decision.ask
    async def malformed(*args, **kwargs):
        answers = await original(*args, **kwargs)
        score = answers["suitability"]
        changes = {"score": float("nan")} if kind == "score" else (
            {"legend": {"0": "wrong"}} if kind == "legend" else {"probabilities": {"0": 1.0}})
        answers["suitability"] = replace(score, **changes)
        return answers
    host.decision.ask = malformed
    with pytest.raises(LayaClientError) as error:
        asyncio.run(adapter.ask(host, questions(), "Synthetic draft"))
    assert error.value.code == "laya_contract_invalid"


@pytest.mark.parametrize("kind", ["missing", "wrong_type", "nan", "wrong_choice", "wrong_probability"])
def test_invalid_answers_never_become_decisions(host, kind):
    original = host.decision.ask
    async def malformed(*args, **kwargs):
        answers = await original(*args, **kwargs)
        if kind == "missing":answers.pop("action")
        elif kind == "wrong_type":answers["action"] = {"choice": "reply"}
        elif kind == "nan":answers["action"] = replace(answers["action"], confidence=float("nan"))
        elif kind == "wrong_choice":answers["action"] = replace(answers["action"], choice="unknown")
        else:answers["action"] = replace(answers["action"], probability=0.1)
        return answers
    host.decision.ask = malformed
    with pytest.raises(LayaClientError) as error:
        asyncio.run(LayaDecisionClient(mode="astrbot", context=host).probe())
    assert error.value.code == "laya_contract_invalid"


def test_store_keeps_remote_credentials_when_switching_to_official(tmp_path, host):
    store = LayaSettingsStore(tmp_path)
    initial = store.update("initial", {"mode": "remote", "endpoint": "https://example.test/v1/systemone", "api_key": "synthetic-key"})
    current = store.update(initial["revision"], {"mode": "astrbot", "astrbot_provider_id": "decision/test"})
    service = LayaDecisionService(store, client_factory=laya_client_factory(host))
    assert current["astrbot_provider_id"] == "decision/test"
    assert service.status()["astrbot_providers"][0]["id"] == "decision/test"
    assert store.raw()["api_key"] == "synthetic-key"
    assert "synthetic-key" not in json.dumps(service.status())
    assert asyncio.run(service.test())["status"] == "ok"


def test_official_backend_records_its_origin_without_a_database_migration(tmp_path, host):
    from companion_v2.storage.runtime import StorageRuntime
    store = LayaSettingsStore(tmp_path / "settings")
    store.update("initial", {"enabled": True, "mode": "astrbot", "astrbot_provider_id": "decision/test"})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="decision-test")
    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="synthetic-origin",
            client_factory=laya_client_factory(host))
        result = asyncio.run(service.decide({"scope_kind": "group", "user_text": "Synthetic greeting"}))
        assert result["status"] == "ok"
        row = service.recent()["events"][0]
        assert row["mode"] == "remote"
        assert row["processing"]["backend"] == "astrbot"
        assert row["processing"]["provider_id"] == "decision/test"
        assert row["response"]["value"]["usage_availability"] == "unavailable"
    finally:
        runtime.close()


class GatewayRestrictionTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def test_cached_verdict_identity_changes_with_model_prompt_and_binding(self):
        identity = lambda: self.router.cache_identity(self.context, "fixture.read", at=self.clock.now())
        before = identity()
        self.assertEqual(before, identity())
        self.patch_model(provider="fallback", fallback="")
        changed = identity()
        self.assertNotEqual(before, changed)
        self.patch_model(prompt="A changed review policy")
        prompt_changed = identity()
        self.assertNotEqual(changed, prompt_changed)
        provider = self.router.providers["fallback"]
        self.router.providers = MappingProxyType({**self.router.providers,
            "fallback": replace(provider, binding=replace(provider.binding, generation="changed"))})
        self.assertNotEqual(prompt_changed, identity())

    async def test_task_restricted_provider_is_rejected_before_dispatch(self):
        restricted = replace(self.provider("primary"), task_kinds=("proactive.judge",))
        self.gateway_with(restricted, self.provider("fallback"))
        with self.assertRaises(Exception) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "model_capability_mismatch")
        self.assertEqual(self.called, [])
        with self.assertRaises(ValueError):
            validate_targets(checked_rules('[{"id":"switch","provider_id":"primary","keywords":["hello"]}]'),
                self.router.providers, capabilities=("text",), task_kind="memory.reflect")

    async def test_unmetered_provider_cannot_bypass_hard_budget(self):
        self.gateway_with(replace(self.provider("primary"), supports_hard_budget=False), self.provider("fallback"))
        with self.assertRaises(Exception) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "model_budget_unavailable")
        self.assertEqual(self.called, [])
        self.assertEqual(self.call_rows(), ())


class ProactiveDecisionTests(JudgeFixture, unittest.IsolatedAsyncioTestCase):
    async def official_host(self):
        self.types_patch = patch.object(adapter, "_types", return_value=TYPES)
        self.types_patch.start();self.addCleanup(self.types_patch.stop)
        self.native_host = Host()
        host = await self.judged_host()
        app = host.application
        app.router.providers = MappingProxyType({**app.router.providers,
            "decision/test": adapter.AstrBotDecisionModel(self.native_host, "decision/test").local_provider()})
        app.gateway.ledger.policy = replace(app.gateway.ledger.policy, enabled=False)
        manager = ModelManagement(app, self.contact_context())
        task = next(t for t in manager.status(self.core).unwrap()["tasks"] if t["kind"] == "proactive.judge")
        manager.configure(self.core, {"kind":"proactive.judge", "layer":"role", "operation_key":"decision-route",
            "edit_token":task["edit_token"], "changes":{"provider":"decision/test", "fallback":""}, "reset":[]}).unwrap()
        return app

    async def test_decision_review_keeps_sealed_body_and_unknown_usage(self):
        app = await self.official_host()
        record = await self.judged(app)
        self.assertTrue(record["judge"]["allowed"], record["judge"])
        self.assertEqual(len(self.native_host.calls), 1)
        self.assertEqual(self.judge_calls, [])
        self.assertIn("persona", self.native_host.calls[0][1])
        self.assertIn("materials", self.native_host.calls[0][1])
        call = app.runtime.read_one("SELECT * FROM model_calls WHERE call_id=?", (record["judge"]["model_call_id"],))
        self.assertIsNone(call["actual_tokens"])
        self.assertIsNone(call["actual_cost"])
        await self.delivery_state(app, self.contacts(app)[0]["task_id"], "delivered")
        self.assertEqual(self.sent[-1].text, "刚想起上次的话题。")

    async def test_problem_reason_withholds_even_when_send_head_agrees(self):
        app = await self.official_host()
        self.native_host.reason = "unsupported"
        record = await self.judged(app)
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(self.deliveries(app), ())

    async def test_suitability_uses_existing_threshold(self):
        app = await self.official_host()
        self.native_host.level = 2  # 50/100, below the existing default threshold of 62.
        record = await self.judged(app)
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(record["judge"]["score"], 50)
        self.assertEqual(self.deliveries(app), ())

    async def test_failed_decision_has_no_delivery_or_automatic_second_call(self):
        app = await self.official_host()
        self.native_host.fail = RuntimeError("synthetic failure")
        record = await self.judged(app)
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(self.deliveries(app), ())
        self.assertEqual(len(self.native_host.calls), 1)

    async def test_changed_provider_before_dispatch_never_calls_it(self):
        app = await self.official_host()
        self.native_host.native = Native()
        record = await self.judged(app)
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(self.native_host.calls, [])

    async def test_provider_changed_during_review_withholds_the_result(self):
        app = await self.official_host()
        original = self.native_host.decision.ask
        async def changed(*args, **kwargs):
            answer = await original(*args, **kwargs)
            self.native_host.native = Native()
            return answer
        self.native_host.decision.ask = changed
        record = await self.judged(app)
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(len(self.native_host.calls), 1)
        self.assertEqual(self.deliveries(app), ())
