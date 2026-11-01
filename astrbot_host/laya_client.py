"""Adapters for the official Laya Python API and its SystemOne HTTP protocol."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
import math
import os
from pathlib import Path
import shlex
import sys
import threading
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import warnings

from companion_v2.decision.contract import (CONFIGURABLE_DECISION_FIELDS, DECISION_DEFAULTS, LayaDecision,
                       DecisionContractError, deterministic_skip_reason, parse_decision)
from companion_v2.decision.prompt import (DECISION_QUESTIONS, INTERVENTION_CHOICES, build_state,
    decision_questions, facts_document, intervention_question, render_intervention_state, render_model_state)
from companion_v2.decision.store import MODE_MODELS, cached_multilingual_model


logger = logging.getLogger(__name__)
_LOCAL_ROUTER_LOCK = threading.Lock()
_LOCAL_ROUTERS: dict[str, tuple[object, threading.Lock]] = {}
_LOCAL_WARMUP_TASKS = {}
_LOCAL_READY = set()
_LOCAL_INFERENCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="companion-laya")
_LOCAL_PREDICTION_TRACE = threading.local()
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_MODEL = MODE_MODELS["local"]
SYSTEMONE_PATH = "/v1/systemone"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
WORKERS_AI_PATH = "/ai/run"


from companion_v2.decision.client import LayaClientError


def _endpoint(value: str, target: str = SYSTEMONE_PATH) -> str:
    """Normalise a service address to the exact path the selected backend posts to."""
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LayaClientError("laya_endpoint_invalid", "Laya 服务地址必须是 HTTP(S) 地址")
    path = parsed.path.rstrip("/")
    if not path.endswith(target):
        path += target[len("/v1"):] if path.endswith("/v1") else target
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _workers_ai_endpoint(value: str) -> str:
    """Cloudflare Workers AI accepts the model in the body of ``<account>/ai/run``.

    Unlike SystemOne and the OpenAI-compatible gateway, this endpoint must not
    receive a ``/v1/...`` suffix, so the operator may paste the run address, the
    ``/ai`` root, or only the account base.
    """
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LayaClientError("laya_endpoint_invalid", "Laya 服务地址必须是 HTTP(S) 地址")
    path = parsed.path.rstrip("/")
    if path.endswith(WORKERS_AI_PATH):
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    if "/v1/" in path or path.endswith("/v1"):
        raise LayaClientError(
            "laya_endpoint_invalid",
            "Workers AI 地址不需要 /v1 后缀；请填 "
            "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/run")
    if path.endswith("/ai"):
        path += "/run"
    elif "/accounts/" in path:
        path += WORKERS_AI_PATH
    else:
        raise LayaClientError(
            "laya_endpoint_invalid",
            "Cloudflare Workers AI 地址应形如 "
            "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/run")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _answers_document(value):
    """Accept the answers document, or a JSON string carrying it, inside ``result``."""
    if not isinstance(value, dict):
        return {"answers": {}}
    if isinstance(value.get("answers"), dict):
        return value
    for key in ("response", "content", "text", "output"):
        text = value.get(key)
        if isinstance(text, str) and text.strip().startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("answers"), dict):
                return parsed
    return value


def _source_key(local_model_path=""):
    return str(Path(local_model_path).expanduser().resolve()) if local_model_path.strip() else DEFAULT_MODEL


def _cuda_available():
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _require_cuda():
    if not _cuda_available():
        raise LayaClientError("laya_gpu_unavailable", "当前 AstrBot Python 的 PyTorch 不支持 CUDA，已跳过本地 Laya")


def _local_router(local_model_path=""):
    _require_cuda()
    source = _source_key(local_model_path)
    with _LOCAL_ROUTER_LOCK:
        cached = _LOCAL_ROUTERS.get(source)
        if cached is not None:
            return cached
        try:
            from laya import Router
        except ImportError as error:
            raise LayaClientError(
                "laya_package_missing",
                "当前 AstrBot Python 环境未安装 Laya；请在模型页复制安装命令并重启 AstrBot",
            ) from error
        try:
            installed = Path(source) if local_model_path.strip() else cached_multilingual_model()
            if local_model_path.strip() and not all((installed / name).is_file() for name in (
                    "rl_agent_config.json", "model.safetensors", "tokenizer/tokenizer.json", "encoder/config.json")):
                raise LayaClientError("laya_model_path_invalid", "本地检查点缺少完整的 Laya 权重、配置或分词器")
            options = {"models": {DEFAULT_MODEL: str(installed)}} if installed is not None else {}
            router = Router(default=DEFAULT_MODEL, max_loaded=1, device="cuda", **options)
        except LayaClientError:
            raise
        except Exception as error:
            raise LayaClientError("laya_initialization_failed", f"Laya Router 初始化失败：{type(error).__name__}: {error}") from error
        cached = (router, threading.Lock())
        # Global settings select one model at a time. In-flight calls retain
        # their own router, while future calls use the newly selected source.
        _LOCAL_ROUTERS.clear()
        _LOCAL_READY.clear()
        _LOCAL_ROUTERS[source] = cached
        return cached


def _local_predict(state: str, questions: dict, *, local_model_path=""):
    router_started = perf_counter()
    router, lock = _local_router(local_model_path)
    router_setup_ms = round((perf_counter() - router_started) * 1000)
    lock_started = perf_counter()
    try:
        with lock:
            lock_wait_ms = round((perf_counter() - lock_started) * 1000)
            loader = getattr(router, "load", None)
            model_load_started = perf_counter()
            agent = loader(DEFAULT_MODEL) if callable(loader) else None
            model_load_ms = round((perf_counter() - model_load_started) * 1000)
            if not str(getattr(agent, "device", "")).lower().startswith("cuda"):
                raise LayaClientError("laya_gpu_unavailable", "Laya 模型没有加载到 CUDA 设备，已跳过本地推理")
            fallback_before = getattr(agent, "cpu_fallback_count", None)
            device_before = str(getattr(agent, "device", "unknown"))
            prediction_started = perf_counter()
            # Router.default is only a language-detection fallback. JSON field
            # names can otherwise route Chinese messages to another checkpoint.
            with warnings.catch_warnings():
                # Laya retries CUDA OOM on CPU. Promote that exact warning so the
                # retry is interrupted before any CPU inference can begin.
                warnings.filterwarnings("error", message=r"Warning: GPU memory exceeded during inference\..*",
                                       category=RuntimeWarning)
                result = router.predict(render_model_state(state), questions, model=DEFAULT_MODEL)
            prediction_ms = round((perf_counter() - prediction_started) * 1000)
            fallback_after = getattr(agent, "cpu_fallback_count", None)
            device_after = str(getattr(agent, "device", "unknown"))
            fallback_count = (max(0, int(fallback_after) - int(fallback_before))
                              if isinstance(fallback_before, int) and isinstance(fallback_after, int) else None)
            _LOCAL_PREDICTION_TRACE.value = {
                "router_setup_ms": router_setup_ms,
                "lock_wait_ms": lock_wait_ms,
                "model_load_ms": model_load_ms,
                "router_predict_ms": prediction_ms,
                "device": device_after if device_after != "unknown" else device_before,
                "cpu_fallback_count": fallback_count,
                "cpu_fallback_reason_type": (type(getattr(agent, "last_fallback_reason", None)).__name__
                                             if fallback_count else None),
            }
            return result
    except LayaClientError:
        raise
    except Exception as error:
        _LOCAL_PREDICTION_TRACE.value = {
            "router_setup_ms": router_setup_ms,
            "lock_wait_ms": round((perf_counter() - lock_started) * 1000),
            "prediction_failed": True,
        }
        raise LayaClientError("laya_inference_failed", f"Laya 本地推理失败：{type(error).__name__}: {error}") from error


async def _run_local_predict(state: str, questions: dict, *, local_model_path=""):
    loop = asyncio.get_running_loop()
    queued_at = perf_counter()
    profile = {}

    def run():
        started = perf_counter()
        _LOCAL_PREDICTION_TRACE.value = {}
        try:
            options = {"local_model_path": local_model_path} if local_model_path.strip() else {}
            return _local_predict(state, questions, **options)
        finally:
            profile.update(getattr(_LOCAL_PREDICTION_TRACE, "value", {}))
            profile["executor_queue_ms"] = round((started - queued_at) * 1000)
            profile["worker_ms"] = round((perf_counter() - started) * 1000)
            _LOCAL_PREDICTION_TRACE.value = {}

    result = await loop.run_in_executor(_LOCAL_INFERENCE_EXECUTOR, run)
    return result, profile


async def _run_local_load(local_model_path=""):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_LOCAL_INFERENCE_EXECUTOR, _load_local_model, local_model_path)


def _load_local_model(local_model_path=""):
    _require_cuda()
    router, lock = _local_router(local_model_path)
    with lock:
        agent = router.load(DEFAULT_MODEL)
        device = str(getattr(agent, "device", "unknown"))
        if not device.lower().startswith("cuda"):
            raise LayaClientError("laya_gpu_unavailable", "Laya 模型没有加载到 CUDA 设备，已跳过本地预热")
        return device


def _torch_install_command():
    executable = str(Path(sys.executable).resolve())
    if os.name == "nt":
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        return (f"& {quote(executable)} -m pip install --force-reinstall "
                "--index-url https://download.pytorch.org/whl/cu128 torch")
    return (shlex.quote(executable) + " -m pip install --force-reinstall "
            "--index-url https://download.pytorch.org/whl/cu128 torch")


def _torch_runtime_status():
    """Expose the installed torch build so the page can explain CUDA failures."""
    status = {"torch_version": "", "cuda_version": "", "cuda_available": False,
              "torch_install_command": _torch_install_command()}
    try:
        import torch
        status["torch_version"] = str(getattr(torch, "__version__", "") or "")
        status["cuda_available"] = bool(torch.cuda.is_available())
        status["cuda_version"] = str(getattr(torch.version, "cuda", "") or "")
    except Exception:
        pass
    return status


def local_runtime_status(*, local_model_path=""):
    runtime = _torch_runtime_status()
    if not runtime["cuda_available"]:
        return {**runtime, "local_runtime_state": "gpu_unavailable"}
    source = _source_key(local_model_path)
    task = _LOCAL_WARMUP_TASKS.get(source)
    if task is not None and not task.done():
        return {**runtime, "local_runtime_state": "warming"}
    ready = source in _LOCAL_READY and source in _LOCAL_ROUTERS
    return {**runtime, "local_runtime_state": "ready" if ready else "not_loaded"}


def start_laya_warmup(store):
    """Load installed weights once in the background, independently of reply deadlines."""
    try:
        values = store.raw()
        if not values["enabled"] or values["mode"] != "local" or not any(values["decision_fields"].values()):
            return None
        local_model_path = values["local_model_path"]
        source = _source_key(local_model_path)
        if not _cuda_available():
            logger.info("[CompanionV2] Laya 本地预热跳过 reason=cuda_unavailable")
            return None
        if not local_model_path.strip() and cached_multilingual_model() is None:
            return None
        if local_runtime_status(local_model_path=local_model_path)["local_runtime_state"] == "ready":
            return None
        loop = asyncio.get_running_loop()
    except Exception as error:
        logger.warning("[CompanionV2] Laya 预热未启动 code=%s", type(error).__name__)
        return None
    task = _LOCAL_WARMUP_TASKS.get(source)
    if task is not None and not task.done():
        return task

    async def warmup():
        logger.info("[CompanionV2] Laya 本地模型预热开始 model=%s source=%s", DEFAULT_MODEL, source)
        try:
            initialization_started = perf_counter()
            device = await _run_local_load(local_model_path)
            initialization_ms = round((perf_counter() - initialization_started) * 1000)
            facts = {
                "message_kind": "text",
                "user_text": "我这边在测试连接，请按当前语义分类问题作答。",
                "scope_kind": "test",
                "connection_test": True,
            }
            enabled_fields = {name: True for name in CONFIGURABLE_DECISION_FIELDS}
            state = build_state(facts_document(facts), enabled_fields)
            questions = decision_questions(facts, enabled_fields)
            inference_started = perf_counter()
            # Prime device kernels with synthetic facts before real turns arrive.
            options = {"local_model_path": local_model_path} if local_model_path.strip() else {}
            result, profile = await _run_local_predict(state, questions, **options)
            _decision_from_result(result, enabled_fields)
            inference_warmup_ms = round((perf_counter() - inference_started) * 1000)
            _LOCAL_READY.add(source)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("[CompanionV2] Laya 本地模型预热失败 code=%s", getattr(error, "code", type(error).__name__))
        else:
            logger.info("[CompanionV2] Laya 本地模型就绪 model=%s device=%s initialization_ms=%s inference_warmup_ms=%s",
                        DEFAULT_MODEL, device, initialization_ms, inference_warmup_ms)
            logger.debug("[CompanionV2][开发者追踪] Laya 预热推理分段 profile=%s", profile)

    task = loop.create_task(warmup(), name="companion-laya-warmup")
    _LOCAL_WARMUP_TASKS[source] = task
    return task


async def cancel_laya_warmup():
    """Retire the background waiter on unload; its read-only load may finish."""
    tasks = tuple(_LOCAL_WARMUP_TASKS.values())
    _LOCAL_WARMUP_TASKS.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _parse_document(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise LayaClientError("laya_response_invalid", "Laya 返回的不是有效 JSON") from error
    if type(value) is not dict:
        raise LayaClientError("laya_response_invalid", "Laya 返回的 JSON 不是对象")
    return value


def _decision_from_result(document: dict, enabled_fields=None) -> LayaDecision:
    answers = document.get("answers")
    if type(answers) is not dict:
        raise LayaClientError("laya_response_invalid", "Laya 响应缺少 answers 对象")
    enabled = enabled_fields or {name: True for name in CONFIGURABLE_DECISION_FIELDS}
    choices = dict(DECISION_DEFAULTS)
    for name in DECISION_QUESTIONS:
        if not enabled.get(name, True):
            continue
        answer = answers.get(name)
        choice = answer.get("choice") if type(answer) is dict else None
        if type(answer) is not dict or answer.get("type") != "choice" or type(choice) is not str:
            raise LayaClientError("laya_response_invalid", f"Laya 响应缺少 choice 分类：{name}")
        choices[name] = choice
    choices["reason_codes"] = []
    try:
        decision = parse_decision(choices)
    except DecisionContractError as error:
        raise LayaClientError("laya_contract_invalid", str(error)) from error
    # Laya's calibrated action probability is transport metadata, not a new
    # decision field. Prefer `answer_confidence`; the strict SystemOne wire
    # contract may retain only the full `probabilities` map for choice answers.
    return replace(decision, action_confidence=_action_answer_confidence(answers.get("action")))


def _action_answer_confidence(answer) -> float | None:
    if type(answer) is not dict:
        return None

    def probability(value):
        if (type(value) not in (int, float) or isinstance(value, bool)
                or not math.isfinite(value) or not 0.0 <= value <= 1.0):
            return None
        return float(value)

    calibrated = probability(answer.get("answer_confidence"))
    if calibrated is not None:
        return calibrated
    probabilities, choice = answer.get("probabilities"), answer.get("choice")
    if type(probabilities) is dict and type(choice) is str:
        return probability(probabilities.get(choice))
    return None


def _safe_error_text(raw: bytes, secret: str) -> str:
    try:
        text = raw.decode("utf-8", errors="replace")
        document = json.loads(text)
        if isinstance(document, dict):
            errors = document.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = errors[0].get("message")
                text = (str(message) if isinstance(message, str) and message.strip()
                        else json.dumps(errors[0], ensure_ascii=False))
            else:
                detail = document.get("detail") or document.get("error") or document
                text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    except (TypeError, ValueError):
        text = raw.decode("utf-8", errors="replace")
    if secret:
        text = text.replace(secret, "<hidden>")
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())[:1000]


class _SystemOneTransport:
    """Official Laya SystemOne protocol: the decision document is the whole body."""

    def __init__(self, model: str):
        self.model = model

    def payload(self, model_state: str, questions: dict) -> dict:
        return {"state": model_state, "questions": questions, "model": self.model}

    def document(self, raw: str) -> dict:
        return _parse_document(raw)


class _JevTransport:
    """System One decisions carried by an OpenAI-compatible chat completion.

    A gateway that serves a decisions model next to chat models (New API,
    OpenRouter, Venice) keeps the same state/questions document, but accepts it
    only as the text of one user message. The answer arrives as JSON text in
    ``choices[0].message.content`` and is decoded into the same document shape,
    so both remote backends share one contract conversion.
    """

    def __init__(self, model: str):
        self.model = model

    def payload(self, model_state: str, questions: dict) -> dict:
        decision = {"model": self.model, "state": model_state, "questions": questions}
        return {"model": self.model, "messages": [{"role": "user", "content": json.dumps(
            decision, ensure_ascii=False, separators=(",", ":"))}]}

    def document(self, raw: str) -> dict:
        envelope = _parse_document(raw)
        choices = envelope.get("choices")
        message = choices[0].get("message") if type(choices) is list and choices else None
        content = message.get("content") if type(message) is dict else None
        if type(content) is not str or not content.strip():
            raise LayaClientError("laya_response_invalid", "Jev 响应缺少 choices[0].message.content")
        return _parse_document(content)


class _WorkersAITransport:
    """Cloudflare Workers AI: the decision document travels inside ``input``."""

    def __init__(self, model: str):
        self.model = model

    def payload(self, model_state: str, questions: dict) -> dict:
        return {"model": self.model, "input": {"state": model_state, "questions": questions}}

    def document(self, raw: str) -> dict:
        envelope = _parse_document(raw)
        if envelope.get("success") is False:
            errors = envelope.get("errors")
            message = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("message") or "")
            raise LayaClientError("laya_response_invalid",
                                  "Cloudflare Workers AI 拒绝了该次判断" + (f"：{message}" if message else ""))
        return _answers_document(envelope.get("result"))


class LayaDecisionClient:
    runtime_status = staticmethod(local_runtime_status)

    def __init__(self, *, mode: str = "local", endpoint: str = "", api_key: str = "",
                 timeout_seconds: int = 8, local_model_path: str = "", model: str = ""):
        if mode not in MODE_MODELS:
            raise LayaClientError("laya_mode_invalid",
                                  "Laya 接入方式必须是 local、remote、jev 或 workers_ai")
        self.mode = mode
        self.local_model_path = local_model_path
        self.model = str(model or "").strip() or MODE_MODELS[mode]
        self.endpoint, self.transport = "", None
        if mode == "remote":
            self.endpoint, self.transport = _endpoint(endpoint), _SystemOneTransport(self.model)
        elif mode == "jev":
            self.endpoint = _endpoint(endpoint, CHAT_COMPLETIONS_PATH)
            self.transport = _JevTransport(self.model)
        elif mode == "workers_ai":
            self.endpoint = _workers_ai_endpoint(endpoint)
            self.transport = _WorkersAITransport(self.model)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.last_trace = {}

    async def decide(self, facts: dict, *, enabled_fields=None) -> LayaDecision:
        self.last_trace = {}
        skip = deterministic_skip_reason(facts)
        if skip is not None:
            raise LayaClientError("laya_rule_skip", skip)
        enabled = {name: True for name in CONFIGURABLE_DECISION_FIELDS}
        if enabled_fields is not None:
            enabled.update({name: value for name, value in enabled_fields.items()
                            if name in enabled and type(value) is bool})
        if not any(enabled.values()):
            raise LayaClientError("laya_no_decision_fields", "没有启用任何 Laya 决策分类")
        local_runtime = None
        state = build_state(facts_document(facts), enabled)
        questions = decision_questions(facts, enabled)
        model_state = render_model_state(state)
        self.last_trace = {
            "request": {"model": self.model, "state": model_state, "questions": questions, "sent": False},
            "response": None,
            "processing": {"stages": [{"name": "request_preparation", "status": "ok"}]},
        }
        logger.debug("[CompanionV2][开发者追踪] Laya 输入 mode=%s state=%s model_state=%s questions=%s",
                     self.mode, state[:16000], render_model_state(state)[:16000],
                     json.dumps(questions, ensure_ascii=False, separators=(",", ":")))
        if self.mode == "local":
            _require_cuda()
            if local_runtime_status(local_model_path=self.local_model_path)["local_runtime_state"] == "warming":
                raise LayaClientError("laya_initializing", "Laya 本地模型正在预热，本轮继续普通回复流程")
            try:
                self.last_trace["request"]["sent"] = True
                options = {"local_model_path": self.local_model_path} if self.local_model_path.strip() else {}
                raw_result, local_runtime = await asyncio.wait_for(
                    _run_local_predict(state, questions, **options), timeout=self.timeout_seconds)
            except asyncio.TimeoutError as error:
                raise LayaClientError("laya_inference_timeout", "Laya 本地判断超时，本轮继续普通回复流程") from error
            self.last_trace["response"] = {
                "format": "python_object",
                "body": _diagnostic_response_text(raw_result),
                "value": raw_result if type(raw_result) is dict else None,
            }
        else:
            self.last_trace["request"]["sent"] = True
            raw_body = await self._post(model_state, questions)
            self.last_trace["response"] = {"format": "json", "body": raw_body}
            raw_result = self.transport.document(raw_body)
            self.last_trace["response"]["value"] = raw_result
        if type(raw_result) is not dict:
            raise LayaClientError("laya_response_invalid", "Laya predict 返回的结果不是对象")
        logger.debug("[CompanionV2][开发者追踪] Laya 原始答案 mode=%s response=%s",
                     self.mode, repr(raw_result)[:20000])
        try:
            decision = _decision_from_result(raw_result, enabled)
        except LayaClientError:
            self.last_trace["processing"]["stages"].append({"name": "contract_conversion", "status": "failed"})
            raise
        contract_decision = decision.document()
        transformations = []
        if self.mode == "local":
            _LOCAL_READY.add(_source_key(self.local_model_path))
        if facts.get("scope_kind") != "group" and decision.action == "interject":
            decision = replace(decision, action="reply")
            transformations.append({"name": "private_interject_to_reply", "from": "interject", "to": "reply"})
        self.last_trace["processing"] = {
            "stages": [
                {"name": "inference", "status": "ok"},
                {"name": "contract_conversion", "status": "ok"},
                {"name": "scope_normalization", "status": "ok"},
            ],
            "contract_decision": contract_decision,
            "effective_decision": decision.document(),
            "action_confidence": decision.action_confidence,
            "transformations": transformations,
        }
        if local_runtime is not None:
            self.last_trace["processing"]["local_runtime"] = local_runtime
        logger.debug("[CompanionV2][开发者追踪] Laya 合同转换 result=%s",
                     json.dumps(decision.document(), ensure_ascii=False, separators=(",", ":")))
        return decision

    async def classify_intervention(self, facts: dict) -> str:
        """Classify one coalesced private conversation without mutating role data."""
        if type(facts) is not dict or facts.get("scope_kind") != "private":
            raise LayaClientError("laya_facts_invalid", "role intervention classification requires a private window")
        self.last_trace = {}
        questions = intervention_question(facts)
        state = render_intervention_state(facts)
        model_state = render_model_state(state)
        self.last_trace = {
            "request": {"model": self.model, "state": model_state, "questions": questions, "sent": False},
            "response": None,
            "processing": {"stages": [{"name": "request_preparation", "status": "ok"}]},
        }
        raw_result, local_runtime = None, None
        if self.mode == "local":
            _require_cuda()
            if local_runtime_status(local_model_path=self.local_model_path)["local_runtime_state"] == "warming":
                raise LayaClientError("laya_initializing", "Laya 本地模型正在预热")
            try:
                self.last_trace["request"]["sent"] = True
                options = {"local_model_path": self.local_model_path} if self.local_model_path.strip() else {}
                raw_result, local_runtime = await asyncio.wait_for(
                    _run_local_predict(state, questions, **options), timeout=self.timeout_seconds)
            except asyncio.TimeoutError as error:
                raise LayaClientError("laya_inference_timeout", "Laya 本地干预判断超时") from error
            self.last_trace["response"] = {"format": "python_object",
                "body": _diagnostic_response_text(raw_result),
                "value": raw_result if type(raw_result) is dict else None}
        else:
            self.last_trace["request"]["sent"] = True
            raw_body = await self._post(model_state, questions)
            self.last_trace["response"] = {"format": "json", "body": raw_body}
            raw_result = self.transport.document(raw_body)
            self.last_trace["response"]["value"] = raw_result
        if type(raw_result) is not dict:
            raise LayaClientError("laya_response_invalid", "Laya intervention result is not an object")
        answers = raw_result.get("answers")
        answer = answers.get("intervention_need") if type(answers) is dict else None
        choice = answer.get("choice") if type(answer) is dict and answer.get("type") == "choice" else None
        if type(choice) is not str or choice not in INTERVENTION_CHOICES:
            raise LayaClientError("laya_contract_invalid", "Laya returned an unsupported intervention category")
        if self.mode == "local":
            _LOCAL_READY.add(_source_key(self.local_model_path))
        self.last_trace["processing"] = {"stages": [
            {"name": "inference", "status": "ok"},
            {"name": "contract_conversion", "status": "ok"}],
            "intervention_need": choice}
        if local_runtime is not None:
            self.last_trace["processing"]["local_runtime"] = local_runtime
        return choice

    async def probe(self, *, enabled_fields=None) -> LayaDecision:
        return await self.decide({
            "message_kind": "text",
            "user_text": "我这边在测试连接，请按当前语义分类问题作答。",
            "scope_kind": "test",
            "connection_test": True,
        }, enabled_fields=enabled_fields)

    async def _post(self, model_state: str, questions: dict) -> str:
        body = json.dumps(self.transport.payload(model_state, questions),
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")

        def send() -> str:
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    content = response.read(MAX_RESPONSE_BYTES + 1)
            except HTTPError as error:
                detail = _safe_error_text(error.read(4096), self.api_key)
                # A rejected credential is the operator's to fix, so it keeps its
                # own code instead of the generic HTTP status.
                code = "laya_auth_failed" if error.code in {401, 403} else "laya_http_error"
                raise LayaClientError(code,
                    f"Laya HTTP {error.code}" + (f"：{detail}" if detail else "")) from error
            except (URLError, TimeoutError, OSError) as error:
                raise LayaClientError("laya_transport_failed",
                    f"Laya 连接失败：{type(error).__name__}: {error}") from error
            if len(content) > MAX_RESPONSE_BYTES:
                raise LayaClientError("laya_response_too_large", "Laya 响应超过 1 MiB")
            try:
                return content.decode("utf-8")
            except UnicodeError as error:
                raise LayaClientError("laya_response_invalid", "Laya 响应不是 UTF-8") from error

        try:
            return await asyncio.to_thread(send)
        except LayaClientError:
            raise


def _diagnostic_response_text(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return repr(value)[:32768]
