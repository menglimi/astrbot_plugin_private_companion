from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sys
import types
from urllib.error import HTTPError, URLError

import pytest

from companion_v2.decision.client import LayaClientError
from astrbot_host.laya_client import LayaDecisionClient, _LOCAL_ROUTERS, _decision_from_result
from companion_v2.decision.contract import DecisionContractError, deterministic_skip_reason, parse_decision
from companion_v2.decision.prompt import DECISION_QUESTIONS, build_state
from companion_v2.decision.service import LayaDecisionService
from companion_v2.decision.store import LayaSettingsStore, LayaSettingsError, role_storage_key


def decision_document():
    return {
        "turn_state": "complete",
        "action": "reply",
        "reply_intent": "answer",
        "reply_style": "normal",
        "reply_form": "text",
        "segment_mode": "single",
        "tool_need": "none",
        "reason_codes": [],
    }


def laya_result(overrides=None):
    values = {**decision_document(), **(overrides or {})}
    return {"answers": {key: {"type": "choice", "choice": value}
                        for key, value in values.items() if key != "reason_codes"},
            "model": "multilingual", "usage": {"input_tokens": 12, "output_tokens": 7}}


def test_laya_contract_is_exact_and_numeric_free():
    document = decision_document()
    assert parse_decision(document).document() == document
    with pytest.raises(DecisionContractError):
        parse_decision({**document, "wait_seconds": 2})
    with pytest.raises(DecisionContractError):
        parse_decision({**document, "reason_codes": [2]})
    with pytest.raises(DecisionContractError):
        parse_decision({**document, "reply_form": "none"})


def test_action_confidence_is_transport_metadata_only():
    payload = laya_result()
    payload["answers"]["action"].update({"answer_confidence": 0.6234,
        "probabilities": {"reply": 0.6234, "wait": 0.2, "silent": 0.1,
                          "interject": 0.04, "clarify": 0.0366}})
    decision = _decision_from_result(payload)
    assert decision.action_confidence == 0.6234
    assert decision.document() == decision_document()

    payload["answers"]["action"] = {
        "type": "choice", "choice": "reply",
        "probabilities": {"reply": 0.58, "wait": 0.42},
    }
    assert _decision_from_result(payload).action_confidence == 0.58

    # `confidence` on a choice answer is normalized entropy, not answer
    # probability. It must not be used as a threshold score.
    payload["answers"]["action"] = {"type": "choice", "choice": "reply", "confidence": 0.99}
    assert _decision_from_result(payload).action_confidence is None


def test_followup_confidence_threshold_is_validated_and_persisted(tmp_path):
    store = LayaSettingsStore(tmp_path, "target", "role")
    assert store.read()["followup_confidence_threshold"] == 0

    with pytest.raises(LayaSettingsError) as error:
        store.update(store.raw()["revision"], {"followup_confidence_threshold": 101})
    assert error.value.code == "laya_config_invalid"

    status = store.update(store.read()["revision"], {"followup_confidence_threshold": 55})
    assert status["followup_confidence_threshold"] == 55
    assert store.raw()["followup_confidence_threshold"] == 55


def test_followup_confidence_gate_suppresses_only_weak_automatic_continuations(tmp_path):
    class FixedClient:
        last_trace = {"request": {"model": "multilingual", "sent": True},
                      "response": None, "processing": {}}

        def __init__(self, decision):
            self.decision = decision

        async def decide(self, _facts, *, enabled_fields=None):
            return self.decision

    store = LayaSettingsStore(tmp_path, "target", "role")
    store.update(store.raw()["revision"], {"enabled": True,
                                           "followup_confidence_threshold": 55})
    service = LayaDecisionService(store)
    base = parse_decision(decision_document())
    facts = {"message_kind": "text", "scope_kind": "group", "user_text": "还想再聊一句",
             "candidate_reason": "reply_follow_up"}

    def decide(decision, observed=facts):
        service._client = lambda _values: FixedClient(decision)
        return asyncio.run(service.decide(observed))

    weak = decide(replace(base, action_confidence=0.40))
    assert weak["decision"]["action"] == "silent"
    assert weak["reason"] == "laya_followup_confidence_below_threshold"
    assert weak["confidence_gate"]["status"] == "below_threshold"
    assert weak["confidence_gate"]["original_action"] == "reply"

    strong = decide(replace(base, action_confidence=0.56))
    assert strong["decision"]["action"] == "reply"
    assert strong["confidence_gate"]["status"] == "passed"

    mentioned = decide(replace(base, action_confidence=0.10),
                       {**facts, "candidate_reason": "mention"})
    assert mentioned["decision"]["action"] == "reply"
    assert "confidence_gate" not in mentioned

    unavailable = decide(base)
    assert unavailable["decision"]["action"] == "reply"
    assert unavailable["confidence_gate"]["status"] == "confidence_unavailable"


def test_laya_questions_use_official_choice_types_without_numeric_controls():
    assert set(DECISION_QUESTIONS) == set(decision_document()) - {"reason_codes"}


def test_local_inference_uses_dedicated_executor_and_keeps_runtime_profile(monkeypatch):
    import threading
    from astrbot_host import laya_client

    observed = {}

    def predict(_state, _questions):
        observed["thread"] = threading.current_thread().name
        laya_client._LOCAL_PREDICTION_TRACE.value = {
            "lock_wait_ms": 2,
            "router_predict_ms": 8,
            "device": "cuda:0",
            "cpu_fallback_count": 0,
            "cpu_fallback_reason_type": None,
        }
        return laya_result()

    monkeypatch.setattr(laya_client, "_local_predict", predict)
    client = LayaDecisionClient(mode="local")
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "新问题", "scope_kind": "group"}))

    profile = client.last_trace["processing"]["local_runtime"]
    assert result.action == "reply"
    assert observed["thread"].startswith("companion-laya")
    assert profile["device"] == "cuda:0"
    assert profile["cpu_fallback_count"] == 0
    assert profile["lock_wait_ms"] == 2
    assert profile["router_predict_ms"] == 8
    assert isinstance(profile["executor_queue_ms"], int)
    assert isinstance(profile["worker_ms"], int)


def test_laya_state_uses_stable_fact_order():
    first = {"message_kind": "text", "scope_kind": "private", "user_text": "unfinished input"}
    second = {"user_text": "unfinished input", "message_kind": "text", "scope_kind": "private"}

    assert build_state(first) == build_state(second)
    assert all(question["type"] == "choice" for question in DECISION_QUESTIONS.values())
    assert all("criteria" in question for question in DECISION_QUESTIONS.values())
    assert "seconds" not in str(DECISION_QUESTIONS).lower()
    state = build_state({"message_kind": "text", "user_text": "还没说完，稍等"})
    assert json.loads(state)["facts"]["user_text"] == "还没说完，稍等"


def test_model_state_uses_current_message_and_authorized_context_without_routing_ids():
    from companion_v2.decision.prompt import render_model_state
    facts = {"scope_kind": "group", "user_text": "继续等我补充", "mentioned": True,
             "companion_role_id": "persona-binding-id", "bot_account_id": "bot-account-id",
             "recent_group_messages": [{"speaker_id": "synthetic-member", "text": "此前消息"}],
             "has_image": True}
    rendered = render_model_state(build_state(facts))
    assert json.loads(rendered)["facts"]["user_text"] == "继续等我补充"
    assert "此消息@了机器人" in rendered
    assert "此前消息" in rendered
    assert "附有：图片" in rendered
    assert "persona-binding-id" not in rendered
    assert "bot-account-id" not in rendered


def test_robot_message_and_quote_are_rule_owned():
    for field in ("is_bot_message", "sender_is_bot", "quoted_bot_message", "quotes_bot_message",
                  "has_bot_quote", "referenced_bot_message", "reply_to_bot_message"):
        assert deterministic_skip_reason({field: True}) == "robot_message_reference"
    assert deterministic_skip_reason({"message_kind": "command"}) == "explicit_event_rule"
    assert deterministic_skip_reason({"message_kind": "text", "user_text": "你好"}) is None


def test_runtime_facts_skip_bot_authored_events_and_quotes():
    from types import SimpleNamespace as NS
    from companion_v2.conversation.generation import LocalReplyGenerator

    request = NS(
        event=NS(
            principal=NS(native_account_id="200"),
            scope=NS(account_id="100", kind=NS(value="group")),
            quotes=(NS(sender_id="100"),),
            forwards=(),
        ),
        images=(), audios=(), videos=(),
        context=NS(materials=(NS(purpose="current_source", payload="hello"),)),
    )
    facts = LocalReplyGenerator._laya_facts(request)
    assert facts["quoted_bot_message"] is True
    assert deterministic_skip_reason(facts) == "robot_message_reference"

    request.event.principal.native_account_id = "100"
    facts = LocalReplyGenerator._laya_facts(request)
    assert facts["is_bot_message"] is True
    assert deterministic_skip_reason(facts) == "robot_message_reference"


def test_model_page_and_single_runtime_share_laya_storage_key(tmp_path):
    from types import SimpleNamespace as NS
    from astrbot_host.model_api import ModelWebAPI

    settings = NS(profile_id="private-profile", role_id="role-a")
    controller = NS(data_root=tmp_path, settings=settings,
                    resolved_settings=lambda: settings)
    page_service = ModelWebAPI(controller)._laya_service()
    runtime_store = LayaSettingsStore(tmp_path, settings.profile_id, settings.role_id)
    assert page_service.store.path == runtime_store.path


def test_model_page_and_multi_runtime_share_target_storage_key(tmp_path):
    from types import SimpleNamespace as NS
    from astrbot_host.model_api import ModelWebAPI

    controller = NS(data_root=tmp_path, target_id="target-a", role_id="role-a")
    page_service = ModelWebAPI(controller)._laya_service()
    runtime_store = LayaSettingsStore(tmp_path, "target-a", "role-a")
    assert page_service.store.path == runtime_store.path


def test_every_target_and_persona_share_global_settings_and_conflict_detection(tmp_path):
    private = LayaSettingsStore(tmp_path, "private-a", "role-a")
    group = LayaSettingsStore(tmp_path, "group-b", "role-b")
    initial = group.read()
    saved = private.update(private.read()["revision"], {"enabled": True, "decision_fields": {"action": False},
                                                     "local_model_path": str(tmp_path / "custom-checkpoint")})
    assert group.read() == private.read()
    assert group.read()["configuration_scope"] == "global"
    assert group.path == tmp_path / "decision" / "settings.json"
    assert group.raw()["enabled"] is True
    assert group.raw()["local_model_path"] == str(tmp_path / "custom-checkpoint")
    assert group.raw()["decision_fields"]["action"] is False
    with pytest.raises(LayaSettingsError) as error:
        group.update(initial["revision"], {"enabled": False})
    assert error.value.code == "laya_config_conflict"
    group.update(saved["revision"], {"enabled": False})
    assert private.raw()["enabled"] is False


def test_legacy_latest_configuration_is_adopted_globally_without_changing_originals(tmp_path):
    import os

    directory = tmp_path / "decision" / "roles"
    directory.mkdir(parents=True)
    old = directory / (role_storage_key("private", "old-role") + ".json")
    latest = directory / (role_storage_key("group", "new-role") + ".json")
    old.write_text(json.dumps({"enabled": False, "revision": "old"}), encoding="utf-8")
    latest.write_text(json.dumps({"enabled": True, "mode": "remote", "endpoint": "http://localhost:8000",
                                  "api_key": "legacy-secret", "decision_fields": {"action": False}}), encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(latest, (2, 2))
    originals = (old.read_bytes(), latest.read_bytes())
    private, group = LayaSettingsStore(tmp_path, "private", "old-role"), LayaSettingsStore(tmp_path, "group", "new-role")
    assert private.raw() == group.raw()
    assert group.raw()["api_key"] == "legacy-secret"
    assert group.raw()["enabled"] is True
    group.update(group.raw()["revision"], {"enabled": True})
    assert group.path.is_file()
    assert private.raw()["decision_fields"]["action"] is False
    assert (old.read_bytes(), latest.read_bytes()) == originals


def test_local_router_adapter_calls_predict_and_maps_choices(monkeypatch):
    _LOCAL_ROUTERS.clear()
    monkeypatch.setattr("astrbot_host.laya_client.cached_multilingual_model", lambda: None)
    calls = []

    class Router:
        def __init__(self, **kwargs):
            assert kwargs == {"default": "multilingual", "max_loaded": 1}

        def predict(self, state, questions, *, model):
            assert model == "multilingual"
            calls.append((state, questions))
            return laya_result()

    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=Router))
    client = LayaDecisionClient(mode="local")
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert result.document() == decision_document()
    assert len(calls) == 1
    assert set(calls[0][1]) == set(DECISION_QUESTIONS)
    assert json.loads(calls[0][0])["facts"]["user_text"] == "你好"
    assert client.last_trace["request"]["sent"] is True
    assert client.last_trace["request"]["state"] == calls[0][0]
    assert json.loads(client.last_trace["response"]["body"]) == laya_result()
    assert client.last_trace["processing"]["effective_decision"] == decision_document()
    _LOCAL_ROUTERS.clear()


def test_private_scope_normalizes_an_inapplicable_interjection():
    seen_actions = []

    class Router:
        def __init__(self, **_kwargs):
            pass

        def predict(self, _state, questions, *, model):
            assert model == "multilingual"
            seen_actions.append(set(questions["action"]["criteria"]))
            values = {**decision_document(), "action": "interject"}
            return {"answers": {key: {"type": "choice", "choice": value}
                                 for key, value in values.items() if key != "reason_codes"}}

    _LOCAL_ROUTERS.clear()
    client = LayaDecisionClient(mode="local")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=Router))
        private = asyncio.run(client.decide({"message_kind": "text", "scope_kind": "private", "user_text": "你好"}))
        group = asyncio.run(client.decide({"message_kind": "text", "scope_kind": "group", "user_text": "你好"}))
    assert private.action == "reply"
    assert group.action == "interject"
    assert "interject" not in seen_actions[0]
    assert "interject" in seen_actions[1]
    _LOCAL_ROUTERS.clear()


def test_local_router_import_error_is_actionable(monkeypatch):
    _LOCAL_ROUTERS.clear()
    monkeypatch.delitem(sys.modules, "laya", raising=False)
    original_import = __import__

    def missing(name, *args, **kwargs):
        if name == "laya":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", missing)
    client = LayaDecisionClient(mode="local")
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "hello"}))
    assert error.value.code == "laya_package_missing"


def test_remote_adapter_uses_systemone_contract_and_parses_answers(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps(laya_result(), ensure_ascii=False).encode("utf-8")

    def open_url(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", open_url)
    client = LayaDecisionClient(mode="remote", endpoint="http://127.0.0.1:8000/v1",
                                api_key="private-key", timeout_seconds=17)
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert result.document() == decision_document()
    assert captured["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert captured["headers"]["Authorization"] == "Bearer private-key"
    assert captured["body"]["model"] == "multilingual"
    assert set(captured["body"]["questions"]) == set(DECISION_QUESTIONS)
    assert captured["timeout"] == 17
    assert client.last_trace["request"]["sent"] is True
    assert client.last_trace["request"]["state"] == captured["body"]["state"]
    assert json.loads(client.last_trace["response"]["body"]) == laya_result()
    assert client.last_trace["processing"]["contract_decision"] == decision_document()


def test_workers_ai_adapter_wraps_input_and_parses_result(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"success": True, "result": laya_result()},
                              ensure_ascii=False).encode("utf-8")

    def open_url(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", open_url)
    client = LayaDecisionClient(
        mode="workers_ai",
        endpoint="https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run",
        api_key="cf-key", timeout_seconds=21, model="typesafe/jev-1.14.0")
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert result.document() == decision_document()
    assert captured["url"] == "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run"
    assert captured["body"]["model"] == "typesafe/jev-1.14.0"
    assert set(captured["body"]["input"]) == {"state", "questions"}
    assert captured["body"]["input"]["state"] == client.last_trace["request"]["state"]
    assert set(captured["body"]["input"]["questions"]) == set(DECISION_QUESTIONS)
    assert captured["timeout"] == 21


@pytest.mark.parametrize("endpoint", [
    "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run",
    "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run/",
    "https://api.cloudflare.com/client/v4/accounts/acc-1/ai",
    "https://api.cloudflare.com/client/v4/accounts/acc-1",
])
def test_workers_ai_endpoint_normalisation(endpoint):
    client = LayaDecisionClient(mode="workers_ai", endpoint=endpoint,
                                model="typesafe/jev-1.14.0")
    assert client.endpoint == "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run"


def test_workers_ai_defaults_to_cloudflares_free_decision_model():
    client = LayaDecisionClient(
        mode="workers_ai",
        endpoint="https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run")
    assert client.model == "@cf/cloudflare/clef"


def test_workers_ai_rejects_unrelated_endpoint():
    with pytest.raises(LayaClientError) as error:
        LayaDecisionClient(mode="workers_ai", endpoint="https://example.com/v1",
                           model="typesafe/jev-1.14.0")
    assert error.value.code == "laya_endpoint_invalid"


def test_workers_ai_rejects_a_systemone_style_address():
    """Pasting the SystemOne or chat-completions address must fail loudly."""
    for endpoint in (
        "https://api.cloudflare.com/client/v4/accounts/acc-1/v1/systemone",
        "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/v1/chat/completions",
    ):
        with pytest.raises(LayaClientError) as error:
            LayaDecisionClient(mode="workers_ai", endpoint=endpoint)
        assert error.value.code == "laya_endpoint_invalid"


def test_workers_ai_parses_json_string_result_and_reports_errors(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"success": True, "result": {
                "response": json.dumps(laya_result(), ensure_ascii=False)}},
                ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", lambda request, timeout: Response())
    client = LayaDecisionClient(mode="workers_ai",
                                endpoint="https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run",
                                model="typesafe/jev-1.14.0")
    assert asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"})).document() == decision_document()

    class Rejected:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"success": False, "errors": [
                {"code": 2021, "message": "Insufficient balance"}]},
                ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", lambda request, timeout: Rejected())
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert error.value.code == "laya_response_invalid"
    assert "Insufficient balance" in error.value.reason


def test_call_log_persists_model_input_raw_answer_and_processed_decision(tmp_path):
    from companion_v2.storage.runtime import StorageRuntime

    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    status = store.read()
    store.update(status["revision"], {"enabled": True})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-diagnostics-test")

    class RecordedClient:
        last_trace = {
            "request": {"model": "multilingual", "sent": True, "state": '{"facts":{"user_text":"hello"}}',
                        "questions": {"action": {"type": "choice"}}},
            "response": {"format": "json", "body": '{"answers":{"action":{"choice":"wait"}}}',
                         "value": {"answers": {"action": {"choice": "wait"}}}},
            "processing": {"stages": [{"name": "inference", "status": "ok"}],
                           "contract_decision": {**decision_document(), "action": "wait"},
                           "effective_decision": {**decision_document(), "action": "wait"},
                           "transformations": []},
        }

        async def decide(self, _facts, *, enabled_fields=None):
            return parse_decision({**decision_document(), "action": "wait"})

    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="role-key")
        service._client = lambda _values: RecordedClient()
        service.set_trace(event_ref="reply-" + "c" * 32, source="conversation")
        asyncio.run(service.decide({"message_kind": "text", "user_text": "hello"}))

        event = service.recent()["events"][0]
        assert event["request"]["sent"] is True
        assert event["request"]["state"] == '{"facts":{"user_text":"hello"}}'
        assert event["response"]["body"].startswith('{"answers"')
        assert event["processing"]["effective_decision"]["action"] == "wait"
        assert event["processing"]["status"] == "ok"
    finally:
        runtime.close()


def test_call_diagnostic_json_truncation_respects_column_limit():
    from companion_v2.decision.service import _diagnostic_json

    for value in ('"' * 1000, "\\" * 1000, "\u0000" * 1000):
        encoded = _diagnostic_json({"body": value}, 256)
        assert len(encoded) <= 256
        assert json.loads(encoded)["truncated"] is True


def test_remote_adapter_rejects_openai_chat_shape(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b'{"choices":[]}'

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", lambda *_args, **_kwargs: Response())
    client = LayaDecisionClient(mode="remote", endpoint="https://example.test")
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "hello"}))
    assert error.value.code == "laya_response_invalid"


def test_disabled_decision_fields_are_omitted_and_fall_back_to_plugin_defaults(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"answers": {"reply_style": {"type": "choice", "choice": "playful"}}}).encode()

    def open_url(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", open_url)
    enabled = {name: False for name in decision_document() if name != "reason_codes"}
    enabled["reply_style"] = True
    client = LayaDecisionClient(mode="remote", endpoint="https://example.test")
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "hello"}, enabled_fields=enabled))

    assert set(captured["body"]["questions"]) == {"reply_style"}
    assert json.loads(captured["body"]["state"])["facts"]["user_text"] == "hello"
    expected = {**decision_document(), "reply_style": "playful", "segment_mode": "natural"}
    assert result.document() == expected


def test_decision_field_settings_and_runtime_events_are_persisted(tmp_path):
    from companion_v2.storage.runtime import StorageRuntime

    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    status = store.read()
    fields = dict(status["decision_fields"])
    fields["tool_need"] = False
    status = store.update(status["revision"], {"decision_fields": fields})
    assert status["decision_fields"]["tool_need"] is False

    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-test")
    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="role-key")
        service.set_trace(event_ref="reply-" + "a" * 32, source="conversation")
        result = asyncio.run(service.decide({"message_kind": "text", "user_text": "private message"}))
        assert result == {"status": "skipped", "reason": "laya_disabled"}
        event = service.recent()["events"][0]
        assert event["status"] == "skipped"
        assert event["reason_code"] == "laya_disabled"
        assert event["batch_id"] == "reply-" + "a" * 32
        assert event["enabled_fields"]["tool_need"] is False
        stored = runtime.read_one("SELECT * FROM laya_decision_events WHERE event_id = ?", (event["event_id"],))
        assert "private message" not in str(dict(stored))
    finally:
        runtime.close()


def test_failure_logs_keep_http_status_without_persisting_remote_error_body(tmp_path):
    from companion_v2.storage.runtime import StorageRuntime

    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    status = store.read()
    store.update(status["revision"], {"enabled": True})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-error-test")

    class FailedClient:
        async def decide(self, _facts, *, enabled_fields=None):
            raise LayaClientError("laya_http_error", "Laya HTTP 429：用户私密内容被服务端回显")

    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="role-key")
        service._client = lambda _values: FailedClient()
        service.set_trace(event_ref="reply-" + "b" * 32, source="conversation")
        with pytest.raises(LayaClientError):
            asyncio.run(service.decide({"message_kind": "text", "user_text": "用户私密内容"}))

        event = service.recent()["events"][0]
        assert event["status"] == "failed"
        assert event["reason_code"] == "laya_http_error"
        assert event["reason"] == "Laya HTTP 429。"
        assert "用户私密内容" not in json.dumps(event, ensure_ascii=False)
    finally:
        runtime.close()


def test_store_reports_runtime_and_keeps_api_key_secret(tmp_path, monkeypatch):
    store = LayaSettingsStore(tmp_path, "target", "role")
    status = store.read()
    assert status["default_model"] == "multilingual"
    assert status["mode"] == "local"
    assert status["python_executable"] == str(__import__("pathlib").Path(sys.executable).resolve())
    status = store.update(status["revision"], {"mode": "remote", "endpoint": "http://127.0.0.1:8000",
                                               "api_key": "secret"})
    assert status["api_key_set"] is True
    assert "secret" not in str(status)
    with pytest.raises(ValueError):
        store.update(status["revision"], {"mode": "unknown"})


def test_store_detects_complete_multilingual_hub_snapshot(tmp_path, monkeypatch):
    from companion_v2.decision import store as laya_store

    cache = tmp_path / "hub"
    model = cache / "models--convaiinnovations--laya" / "snapshots" / "revision" / "multilingual"
    monkeypatch.setattr(laya_store, "_model_cache_dir", lambda: cache)
    store = LayaSettingsStore(tmp_path, "target", "role")

    (model / "rl_agent_config.json").parent.mkdir(parents=True)
    (model / "rl_agent_config.json").touch()
    assert store.read()["model_cache_ready"] is False

    for relative in ("model.safetensors", "tokenizer/tokenizer.json", "encoder/config.json"):
        path = model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert store.read()["model_cache_ready"] is True


def test_local_adapter_reuses_complete_download_and_pins_multilingual(tmp_path, monkeypatch):
    from companion_v2.decision import store as laya_store

    cache = tmp_path / "hub"
    model = cache / "models--convaiinnovations--laya" / "snapshots" / "cached" / "multilingual"
    for relative in ("rl_agent_config.json", "model.safetensors", "tokenizer/tokenizer.json", "encoder/config.json"):
        path = model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(laya_store, "_model_cache_dir", lambda: cache)
    calls = []

    class Router:
        def __init__(self, *, models, default, max_loaded):
            assert models["multilingual"] == str(model)
            assert default == "multilingual"

        def predict(self, state, questions, *, model):
            calls.append(model)
            return laya_result()

    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=Router))
    _LOCAL_ROUTERS.clear()
    try:
        decision = asyncio.run(LayaDecisionClient(mode="local").decide(
            {"message_kind": "text", "user_text": "你好", "scope_kind": "group"}))
        assert decision.action == "reply"
        assert calls == ["multilingual"]
    finally:
        _LOCAL_ROUTERS.clear()


def test_old_mode_less_laya_settings_migrate_to_multilingual(tmp_path):
    store = LayaSettingsStore(tmp_path, "target", "role")
    store.directory.mkdir(parents=True)
    store.path.write_text(json.dumps({"endpoint": "http://localhost:8000/v1", "model": "laya",
                                      "api_key": "old-secret", "revision": "old"}), encoding="utf-8")
    values = store.raw()
    assert values["mode"] == "remote"
    assert values["api_key"] == "old-secret"
    assert store.read()["default_model"] == "multilingual"


def test_selected_checkpoint_is_used_instead_of_official_cached_weights(tmp_path, monkeypatch):
    from astrbot_host import laya_client
    model = tmp_path / "fine-tuned"
    for relative in ("rl_agent_config.json", "model.safetensors", "tokenizer/tokenizer.json", "encoder/config.json"):
        path = model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    store = LayaSettingsStore(tmp_path / "settings")
    store.update(store.raw()["revision"], {"enabled": True, "local_model_path": str(model)})

    class Router:
        def __init__(self, *, models, default, max_loaded):
            assert models == {"multilingual": str(model)}

        def predict(self, state, questions, *, model):
            return laya_result({"action": "wait"})

    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=Router))
    monkeypatch.setattr(laya_client, "cached_multilingual_model", lambda: (_ for _ in ()).throw(AssertionError("unexpected official model")))
    _LOCAL_ROUTERS.clear()
    try:
        service = LayaDecisionService(store, client_factory=LayaDecisionClient)
        result = asyncio.run(service.decide({"scope_kind": "group", "user_text": "等我补充"}))
        assert result["decision"]["action"] == "wait"
        assert service.status()["local_runtime_state"] == "ready"
        assert store.read()["model_cache_ready"] is True
    finally:
        _LOCAL_ROUTERS.clear()


def test_missing_selected_checkpoint_has_explicit_error_and_cannot_download_another_model(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=lambda **_options: None))
    _LOCAL_ROUTERS.clear()
    try:
        with pytest.raises(LayaClientError) as error:
            asyncio.run(LayaDecisionClient(local_model_path=str(tmp_path / "missing")).decide(
                {"scope_kind": "private", "user_text": "你好"}))
        assert error.value.code == "laya_model_path_invalid"
    finally:
        _LOCAL_ROUTERS.clear()


def test_background_warmup_is_shared_and_does_not_queue_expired_message_inference(tmp_path, monkeypatch):
    import threading
    from astrbot_host import laya_client
    from companion_v2.storage.runtime import StorageRuntime

    store = LayaSettingsStore(tmp_path / "settings")
    store.update(store.raw()["revision"], {"enabled": True})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-warmup-test")
    service = LayaDecisionService(store, runtime=runtime, origin_key="synthetic-window", client_factory=LayaDecisionClient)
    started, release_load = threading.Event(), threading.Event()
    inference_started, release_inference = threading.Event(), threading.Event()
    monkeypatch.setattr(laya_client, "_LOCAL_WARMUP_TASKS", {})
    monkeypatch.setattr(laya_client, "_LOCAL_READY", set())
    monkeypatch.setattr(laya_client, "cached_multilingual_model", lambda: tmp_path / "installed")

    def load(_path=""):
        started.set()
        release_load.wait(3)
        laya_client._LOCAL_ROUTERS[laya_client.DEFAULT_MODEL] = (None, None)
        return "cpu"

    def synthetic_inference(state, questions, **_options):
        inference_started.set()
        release_inference.wait(3)
        assert "我这边在测试连接" in state
        assert "action" in questions
        return laya_result()

    monkeypatch.setattr(laya_client, "_load_local_model", load)
    monkeypatch.setattr(laya_client, "_local_predict", synthetic_inference)

    async def run():
        task = laya_client.start_laya_warmup(store)
        try:
            assert laya_client.start_laya_warmup(store) is task
            await asyncio.to_thread(started.wait, 2)
            result = await service.decide({"message_kind": "text", "user_text": "你好"})
            assert result == {"status": "skipped", "reason": "laya_initializing"}
            assert service.status()["local_runtime_state"] == "warming"
            row = service.recent()["events"][0]
            assert row["status"] == "skipped"
            assert row["reason_code"] == "laya_initializing"
            release_load.set()
            await asyncio.to_thread(inference_started.wait, 2)
            assert service.status()["local_runtime_state"] == "warming"
        finally:
            release_load.set()
            release_inference.set()
            await asyncio.wait_for(task, 3)
        assert service.status()["local_runtime_state"] == "ready"
        assert laya_client.start_laya_warmup(store) is None

    try:
        asyncio.run(run())
    finally:
        runtime.close()
        laya_client._LOCAL_ROUTERS.clear()


def jev_envelope(document=None):
    """One OpenAI chat completion whose text carries a SystemOne answer document."""
    return {"id": "chatcmpl-jev-test", "object": "chat.completion", "model": "jev-latest",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(document or laya_result(), ensure_ascii=False)}}]}


def jev_response(monkeypatch, payload):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", lambda *_args, **_kwargs: Response())


def test_jev_adapter_wraps_the_decision_document_in_one_chat_message(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps(jev_envelope(), ensure_ascii=False).encode("utf-8")

    def open_url(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", open_url)
    client = LayaDecisionClient(mode="jev", endpoint="https://gateway.test/v1",
                                api_key="jev-key", timeout_seconds=23)
    result = asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    decision_request = json.loads(captured["body"]["messages"][0]["content"])

    assert result.document() == decision_document()
    assert captured["url"] == "https://gateway.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer jev-key"
    assert captured["timeout"] == 23
    assert captured["body"]["model"] == "jev-latest"
    assert captured["body"]["messages"][0]["role"] == "user"
    assert decision_request["model"] == "jev-latest"
    assert set(decision_request["questions"]) == set(DECISION_QUESTIONS)
    assert all("instructions" in question and "criteria" in question
               for question in decision_request["questions"].values())
    assert json.loads(decision_request["state"])["facts"]["user_text"] == "你好"
    assert client.last_trace["request"]["model"] == "jev-latest"
    assert client.last_trace["request"]["sent"] is True
    assert json.loads(client.last_trace["response"]["body"])["object"] == "chat.completion"
    assert client.last_trace["response"]["value"]["answers"]["action"]["choice"] == "reply"
    assert client.last_trace["processing"]["contract_decision"] == decision_document()


def test_jev_endpoint_model_and_mode_are_validated_before_any_request():
    assert LayaDecisionClient(mode="jev", endpoint="https://gateway.test").endpoint == \
        "https://gateway.test/v1/chat/completions"
    assert LayaDecisionClient(mode="jev", endpoint="https://gateway.test/v1").endpoint == \
        "https://gateway.test/v1/chat/completions"
    assert LayaDecisionClient(mode="jev", endpoint="https://gateway.test/v1/chat/completions").endpoint == \
        "https://gateway.test/v1/chat/completions"
    assert LayaDecisionClient(mode="jev", endpoint="https://gateway.test").model == "jev-latest"
    assert LayaDecisionClient(mode="jev", endpoint="https://gateway.test", model="jev-1.14.0").model == \
        "jev-1.14.0"
    # The official backend keeps its own path and checkpoint.
    assert LayaDecisionClient(mode="remote", endpoint="https://laya.test").endpoint == \
        "https://laya.test/v1/systemone"
    assert LayaDecisionClient(mode="remote", endpoint="https://laya.test").model == "multilingual"
    assert LayaDecisionClient(mode="local").endpoint == ""

    with pytest.raises(LayaClientError) as error:
        LayaDecisionClient(mode="openai_structured")
    assert error.value.code == "laya_mode_invalid"
    with pytest.raises(LayaClientError) as error:
        LayaDecisionClient(mode="jev", endpoint="gateway.test")
    assert error.value.code == "laya_endpoint_invalid"


def test_jev_adapter_reports_a_rejected_credential_without_leaking_it(monkeypatch):
    class Rejected:
        def read(self, _limit):
            return b'{"error":{"message":"invalid token jev-key"}}'

        def close(self):
            pass

    def reject(*_args, **_kwargs):
        raise HTTPError("https://gateway.test/v1/chat/completions", 401, "Unauthorized", {}, Rejected())

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", reject)
    client = LayaDecisionClient(mode="jev", endpoint="https://gateway.test", api_key="jev-key")
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert error.value.code == "laya_auth_failed"
    assert "jev-key" not in error.value.reason
    assert "HTTP 401" in error.value.reason


def test_jev_adapter_reports_a_transport_failure(monkeypatch):
    def offline(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", offline)
    client = LayaDecisionClient(mode="jev", endpoint="https://gateway.test")
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert error.value.code == "laya_transport_failed"


def test_jev_adapter_rejects_an_envelope_without_decision_content(monkeypatch):
    for payload in ({"choices": []},
                    {"choices": [{"message": {"role": "assistant", "content": ""}}]},
                    {"choices": [{"message": {"role": "assistant", "content": "我这边不是 JSON"}}]}):
        jev_response(monkeypatch, payload)
        client = LayaDecisionClient(mode="jev", endpoint="https://gateway.test")
        with pytest.raises(LayaClientError) as error:
            asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
        assert error.value.code == "laya_response_invalid"
        # The raw envelope still reaches the diagnostics log for debugging.
        assert json.loads(client.last_trace["response"]["body"]) == payload


def test_jev_adapter_requires_every_enabled_answer(monkeypatch):
    incomplete = laya_result()
    incomplete["answers"].pop("action")
    jev_response(monkeypatch, jev_envelope(incomplete))
    client = LayaDecisionClient(mode="jev", endpoint="https://gateway.test")
    with pytest.raises(LayaClientError) as error:
        asyncio.run(client.decide({"message_kind": "text", "user_text": "你好"}))
    assert error.value.code == "laya_response_invalid"
    assert "action" in error.value.reason
    assert client.last_trace["processing"]["stages"][-1] == {"name": "contract_conversion", "status": "failed"}


def test_workers_ai_decisions_are_recorded_with_the_remote_ledger_mode(tmp_path, monkeypatch):
    """Workers AI must not lose its ledger row to the three-backend CHECK constraint."""
    from companion_v2.storage.runtime import StorageRuntime

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"success": True, "result": laya_result()},
                              ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("astrbot_host.laya_client.urlopen", lambda request, timeout: Response())
    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    store.update(store.raw()["revision"], {
        "enabled": True, "mode": "workers_ai",
        "endpoint": "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run",
        "api_key": "cf-key", "remote_model": "typesafe/jev-1.14.0"})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime",
                                  installation_id="laya-workers-ledger")
    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="role-key",
                                      client_factory=LayaDecisionClient)
        service.set_trace(event_ref="reply-" + "b" * 32, source="conversation")
        result = asyncio.run(service.decide({"message_kind": "text", "user_text": "你好"}))
        assert result["status"] == "ok"
        event = service.recent()["events"][0]
        assert event["status"] == "ok"
        assert event["mode"] == "remote"
        stored = runtime.read_one(
            "SELECT mode, request_json FROM laya_decision_events WHERE event_id = ?",
            (event["event_id"],))
        assert stored["mode"] == "remote"
        assert json.loads(stored["request_json"])["model"] == "typesafe/jev-1.14.0"
    finally:
        runtime.close()


def test_jev_mode_and_remote_model_are_persisted_and_validated(tmp_path):
    store = LayaSettingsStore(tmp_path, "target", "role")
    status = store.read()
    assert status["default_model"] == "multilingual"

    status = store.update(status["revision"], {"mode": "jev", "endpoint": "https://gateway.test/v1",
                                               "api_key": "jev-key", "remote_model": "jev-1.14.0"})
    assert status["mode"] == "jev"
    assert status["remote_model"] == "jev-1.14.0"
    assert status["default_model"] == "jev-1.14.0"
    assert status["api_key_set"] is True
    assert "jev-key" not in str(status)

    status = store.update(status["revision"], {"remote_model": ""})
    assert status["default_model"] == "jev-latest"

    with pytest.raises(LayaSettingsError) as error:
        store.update(status["revision"], {"mode": "openai_structured"})
    assert error.value.code == "laya_config_invalid"


def test_jev_mode_requires_an_endpoint_before_any_request(tmp_path):
    store = LayaSettingsStore(tmp_path, "target", "role")
    store.update(store.raw()["revision"], {"enabled": True, "mode": "jev"})
    service = LayaDecisionService(store, client_factory=LayaDecisionClient)
    with pytest.raises(LayaClientError) as error:
        asyncio.run(service.decide({"message_kind": "text", "user_text": "你好"}))
    assert error.value.code == "laya_endpoint_missing"


def test_jev_decisions_are_recorded_in_the_runtime_ledger(tmp_path, monkeypatch):
    """The ledger DDL must accept the third backend, not just local and remote."""
    from companion_v2.storage.runtime import StorageRuntime

    jev_response(monkeypatch, jev_envelope())
    store = LayaSettingsStore(tmp_path / "settings", "target", "role")
    store.update(store.raw()["revision"], {"enabled": True, "mode": "jev",
                                           "endpoint": "https://gateway.test/v1", "api_key": "jev-key"})
    runtime = StorageRuntime.open(data_root=tmp_path / "runtime", installation_id="laya-jev-ledger")
    try:
        service = LayaDecisionService(store, runtime=runtime, origin_key="role-key",
                                      client_factory=LayaDecisionClient)
        service.set_trace(event_ref="reply-" + "a" * 32, source="conversation")
        result = asyncio.run(service.decide({"message_kind": "text", "user_text": "你好"}))
        assert result["status"] == "ok"
        event = service.recent()["events"][0]
        assert event["status"] == "ok"
        assert event["mode"] == "jev"
        assert event["decision"]["action"] == decision_document()["action"]
        stored = runtime.read_one("SELECT mode, request_json FROM laya_decision_events WHERE event_id = ?",
                                  (event["event_id"],))
        assert stored["mode"] == "jev"
        assert json.loads(stored["request_json"])["model"] == "jev-latest"
    finally:
        runtime.close()
