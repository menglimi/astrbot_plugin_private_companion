"""High-level Laya configuration and decision operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
from time import perf_counter
from uuid import uuid4

from .client import LayaClientError, LayaDecisionClient
from .contract import LayaDecision
from .store import MODE_MODELS, LayaSettingsStore
from companion_v2.foundation.refs import ScopeRef


logger = logging.getLogger(__name__)
_FOLLOWUP_REASONS = frozenset({"active_follow_up", "reply_follow_up"})
_FOLLOWUP_REPLY_ACTIONS = frozenset({"reply", "interject", "clarify"})
_SAFE_SKIP_REASONS = frozenset({"facts_invalid", "robot_message_reference", "explicit_event_rule"})
_SAFE_FAILURE_REASONS = {
    "laya_package_missing": "当前 AstrBot Python 环境未安装 Laya。",
    "laya_gpu_unavailable": "AstrBot 的 PyTorch 未启用 CUDA，本地 Laya 已跳过。",
    "laya_initialization_failed": "Laya Router 初始化失败。",
    "laya_model_path_invalid": "选定的本地检查点目录不完整。",
    "laya_inference_failed": "Laya 本地推理失败。",
    "laya_inference_timeout": "Laya 本地判断超时，本轮继续普通回复流程。",
    "laya_transport_failed": "Laya 服务连接失败。",
    "laya_auth_failed": "判断服务鉴权失败。",
    "laya_response_invalid": "Laya 返回格式无效。",
    "laya_response_too_large": "Laya 响应超过允许大小。",
    "laya_contract_invalid": "Laya 返回了插件合同之外的分类。",
}


_AUTH_TOKEN_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+\S+"), "Bearer <hidden>"),
    (re.compile(r"(?<![A-Za-z0-9])(?:sk|pk|rk|ak)[_-][A-Za-z0-9._-]{6,}"), "<hidden>"),
    (re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_|abk_)[A-Za-z0-9_-]{6,}"), "<hidden>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*([:=])\s*([^\s,;'\"]+)"),
     r"\1\2<hidden>"),
)
_HTTP_REASON_RE = re.compile(r"^Laya HTTP ([1-5][0-9]{2})(?:[：:\s·]+(.*))?$", re.DOTALL)
_AUTH_FALLBACK_RE = re.compile(r"^判断服务(?:拒绝访问|鉴权失败)（HTTP ([1-5][0-9]{2})）。$")
_OBSERVATION_BATCH_CHUNK = 400


def _sanitize_auth_text(text: object, *, api_key: str = "") -> str:
    """Redact credentials/tokens and collapse multi-line whitespace from error text."""
    cleaned = str(text or "")
    if api_key:
        cleaned = cleaned.replace(api_key, "<hidden>")
    for pattern, replacement in _AUTH_TOKEN_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return " ".join(cleaned.replace("\r", " ").replace("\n", " ").split())


def _scalar_code(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _extract_auth_summary(detail: str, *, api_key: str = "") -> str:
    """Extract only whitelisted scalar code/message/detail fields from auth error text."""
    text = detail.strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            document = json.loads(text)
        except (TypeError, ValueError):
            return ""
        if not isinstance(document, dict):
            return ""
        error_obj = document.get("error") if isinstance(document.get("error"), dict) else {}
        detail_obj = document.get("detail") if isinstance(document.get("detail"), dict) else {}
        errors = document.get("errors")
        first_error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        raw_code = (
            _scalar_code(document.get("code"))
            or _scalar_code(error_obj.get("code"))
            or _scalar_code(detail_obj.get("code"))
            or _scalar_code(first_error.get("code"))
        )
        raw_message = ""
        for candidate in (
            document.get("message"),
            error_obj.get("message"),
            document.get("detail") if isinstance(document.get("detail"), str) else None,
            detail_obj.get("message"),
            first_error.get("message"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                raw_message = candidate
                break
        code = _sanitize_auth_text(raw_code, api_key=api_key)
        message = _sanitize_auth_text(raw_message, api_key=api_key)
        if code and message:
            return message if message.startswith(code) else f"{code}: {message}"
        return code or message
    return _sanitize_auth_text(text, api_key=api_key)


def _safe_auth_error_summary(reason: object, *, api_key: str = "") -> str:
    """Format a bounded, credential-free HTTP 401/403 summary with whitelisted detail."""
    raw = str(reason or "").strip()
    if _AUTH_FALLBACK_RE.match(raw):
        return raw
    match = _HTTP_REASON_RE.match(raw)
    status = match.group(1) if match else ""
    detail = (match.group(2) or "").strip() if match else ""
    summary = _extract_auth_summary(detail, api_key=api_key) if match else ""
    if summary:
        prefix = f"Laya HTTP {status} · "
        max_body = 120 - len(prefix)
        if len(summary) > max_body:
            summary = summary[:max(1, max_body - 1)].rstrip() + "…"
        return prefix + summary
    if status == "403":
        return "判断服务拒绝访问（HTTP 403）。"
    if status == "401":
        return "判断服务鉴权失败（HTTP 401）。"
    if status:
        return f"判断服务鉴权失败（HTTP {status}）。"
    return "判断服务鉴权失败。"


def safe_laya_client_error_reason(error: object, *, api_key: str = "") -> str:
    """Format a redacted, bridge-safe error reason for live test/preview payloads."""
    code = getattr(error, "code", "")
    raw_reason = str(getattr(error, "reason", error) or "")
    if code == "laya_auth_failed":
        return _safe_auth_error_summary(raw_reason, api_key=api_key)
    if code == "laya_http_error":
        match = _HTTP_REASON_RE.match(raw_reason.strip())
        if match:
            status = match.group(1)
            detail = _sanitize_auth_text(match.group(2) or "", api_key=api_key)[:200]
            return f"Laya HTTP {status}：{detail}" if detail else f"Laya HTTP {status}。"
        return "Laya HTTP 服务返回错误。"
    cleaned = _sanitize_auth_text(raw_reason, api_key=api_key)
    return cleaned[:240] if cleaned else _safe_log_reason(code, raw_reason)


def _safe_log_reason(reason_code, reason, *, api_key: str = ""):
    if reason_code == "laya_rule_skip":
        return reason if reason in _SAFE_SKIP_REASONS else "程序规则跳过。"
    if reason_code == "laya_http_error":
        match = re.match(r"^Laya HTTP ([1-5][0-9]{2})\b", str(reason or ""))
        return f"Laya HTTP {match.group(1)}。" if match else "Laya HTTP 服务返回错误。"
    if reason_code == "laya_auth_failed":
        return _safe_auth_error_summary(reason, api_key=api_key)
    if reason_code == "laya_disabled":
        return "Laya 当前未启用。"
    if reason_code == "laya_no_decision_fields":
        return "没有启用任何决策分类。"
    if reason_code == "laya_initializing":
        return "本地模型正在预热，本轮继续普通回复流程。"
    return _SAFE_FAILURE_REASONS.get(reason_code, "Laya 调用失败；详细信息请查看 AstrBot 开发者日志。")


def _diagnostic_json(value, limit, *, sort_keys=False):
    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys)
    except (TypeError, ValueError, RecursionError):
        encoded = json.dumps({"unavailable": True}, separators=(",", ":"))
    if len(encoded) <= limit:
        return encoded
    preview = encoded[:max(0, limit - 100)]
    while True:
        bounded = json.dumps({"truncated": True, "preview": preview},
                             ensure_ascii=False, separators=(",", ":"))
        if len(bounded) <= limit:
            return bounded
        preview = preview[:len(preview) // 2]


def _write_failure_detail(error):
    """Describe a row the database refused without quoting the row.

    SQLite's own message names the table, column or CHECK that rejected the write
    (``CHECK constraint failed: mode IN ('local', 'remote')``) and never repeats
    the stored values, so it is the one piece of a rejected ledger write that an
    operator can act on. Anything else keeps its type name only.
    """

    if not isinstance(error, sqlite3.Error):
        return "-"
    detail = " ".join(str(error).split())
    return detail[:160] or "-"


def _intervention_trace(client):
    trace = getattr(client, "last_trace", None)
    if not isinstance(trace, dict):
        return None
    request = trace.get("request") if isinstance(trace.get("request"), dict) else {}
    response = trace.get("response") if isinstance(trace.get("response"), dict) else None
    processing = trace.get("processing") if isinstance(trace.get("processing"), dict) else {}
    return {
        "request": {"model": request.get("model"), "sent": request.get("sent") is True},
        "response": {"format": response.get("format")} if response else None,
        "processing": processing,
    }


def _apply_followup_confidence_gate(decision: LayaDecision, facts: dict, threshold: int):
    """Suppress weak automatic continuations using Laya's calibrated action score.

    The threshold is deliberately scoped to the two follow-up candidates. A
    direct mention or an ordinary interjection is not blocked by this setting;
    those paths keep their existing explicit switches. A missing confidence
    fails open because an older server must not silently stop normal replies.
    """
    reason = facts.get("candidate_reason") if type(facts) is dict else None
    if threshold <= 0 or reason not in _FOLLOWUP_REASONS or decision.action not in _FOLLOWUP_REPLY_ACTIONS:
        return decision, None
    gate = {"candidate_reason": reason, "threshold_percent": threshold,
            "original_action": decision.action}
    if decision.action_confidence is None:
        return decision, {**gate, "status": "confidence_unavailable"}
    confidence = decision.action_confidence
    gate.update({"action_confidence": round(confidence, 4),
                 "action_probability_percent": round(confidence * 100, 2)})
    if confidence * 100 >= threshold:
        return decision, {**gate, "status": "passed"}
    return replace(decision, action="silent"), {**gate, "status": "below_threshold",
                                                "effective_action": "silent"}


def _ledger_mode(mode):
    """The decision ledger predates Workers AI; that backend shares the remote row.

    ``laya_decision_events.mode`` carries a CHECK constraint for the three
    original backends, so a call from the Workers AI transport would otherwise
    lose its whole ledger row instead of being recorded.
    """
    return "remote" if mode == "workers_ai" else mode


_SELECT_EVENT_COLUMNS = """SELECT event_id, origin_key, batch_id, source, status, mode, reason_code,
        reason, elapsed_ms, decision_json, enabled_fields_json, request_json, response_json,
        processing_json, observed_at
    FROM laya_decision_events"""


def _scope_metadata(scope) -> dict | None:
    """Build a compact, secret-free scope attribution dictionary for processing_json."""
    if scope is None:
        return None
    if isinstance(scope, str):
        cleaned = scope.strip()
        return {"scope_key": cleaned} if cleaned else None
    if isinstance(scope, dict):
        scope_key = str(scope.get("scope_key") or "").strip()
        if not scope_key:
            try:
                scope_key = ScopeRef.restore(scope).key
            except (KeyError, TypeError, ValueError):
                scope_key = ""
        kind = str(scope.get("kind") or "").strip()
        native_id = str(scope.get("native_scope_id") or "").strip()
    else:
        raw_key = getattr(scope, "key", None)
        scope_key = str((raw_key() if callable(raw_key) else raw_key) or "").strip()
        kind_obj = getattr(scope, "kind", None)
        kind = str(getattr(kind_obj, "value", kind_obj) or "").strip()
        native_id = str(getattr(scope, "native_scope_id", None) or "").strip()
    result = {}
    if scope_key:
        result["scope_key"] = scope_key
    if kind:
        result["kind"] = kind
    if native_id:
        result["native_scope_id"] = native_id
    return result or None


def _parse_observation_scope(context_json_raw) -> dict | None:
    try:
        ctx = json.loads(context_json_raw) if context_json_raw else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(ctx, dict):
        return None
    audience = ctx.get("audience")
    scope_doc = audience.get("scope") if isinstance(audience, dict) else None
    if not isinstance(scope_doc, dict):
        return None
    return _scope_metadata(scope_doc)


def _public_scope_info(scope_meta: dict | None, *, request_doc=None, source=None) -> dict:
    """Build read-only display metadata without leaking message content."""
    kind = ""
    native_id = ""
    if isinstance(scope_meta, dict):
        kind = str(scope_meta.get("kind") or "").strip()
        native_id = str(scope_meta.get("native_scope_id") or "").strip()
    if not kind and isinstance(request_doc, dict):
        raw_state = request_doc.get("state")
        state_obj = None
        if isinstance(raw_state, dict):
            state_obj = raw_state
        elif isinstance(raw_state, str) and raw_state.strip().startswith("{"):
            try:
                parsed = json.loads(raw_state)
                if isinstance(parsed, dict):
                    state_obj = parsed
            except (TypeError, ValueError):
                state_obj = None
        facts = state_obj.get("facts") if isinstance(state_obj, dict) else None
        if isinstance(facts, dict) and facts.get("scope_kind"):
            kind = str(facts["scope_kind"]).strip()
    if not kind:
        if source == "probe":
            kind = "test"
        elif source == "role_intervention":
            kind = "private"
    info = {}
    if kind:
        info["kind"] = kind
    if native_id:
        info["native_scope_id"] = native_id
    return info


class LayaDecisionService:
    def __init__(self, store: LayaSettingsStore, *, runtime=None, origin_key=None,
                 scope=None, client_factory=None):
        self.store = store
        self.runtime = runtime
        self.origin_key = str(origin_key) if origin_key else None
        self.scope = scope
        self._scope_meta = _scope_metadata(scope)
        self.event_ref = None
        self.source = "conversation"
        self.client_factory = client_factory
        self._last_recorded_event_id = None

    def set_trace(self, event_ref=None, source="conversation"):
        self.event_ref = event_ref
        self.source = source
        self._last_recorded_event_id = None

    def record_application(self, application):
        event_id = self._last_recorded_event_id
        if self.runtime is None or self.origin_key is None or not event_id or type(application) is not dict:
            return
        try:
            with self.runtime.transaction() as connection:
                row = connection.execute("""SELECT processing_json FROM laya_decision_events
                    WHERE event_id = ? AND origin_key = ?""", (event_id, self.origin_key)).fetchone()
                if row is None:
                    return
                try:
                    processing = json.loads(row["processing_json"] or "{}")
                except (TypeError, ValueError):
                    processing = {}
                processing["application"] = application
                connection.execute("UPDATE laya_decision_events SET processing_json = ? WHERE event_id = ?",
                                   (_diagnostic_json(processing, 16384), event_id))
        except Exception as error:
            logger.warning("Laya 决策应用结果写入失败 code=%s detail=%s",
                           type(error).__name__, _write_failure_detail(error))

    def status(self) -> dict:
        status = self.store.read()
        runtime_status = getattr(self.client_factory, "runtime_status", None)
        if status["mode"] == "local" and callable(runtime_status):
            status.update(runtime_status(local_model_path=status["local_model_path"]))
        return status

    def save(self, revision, changes) -> dict:
        return self.store.update(revision, changes)

    def _client(self, values: dict) -> LayaDecisionClient:
        if values["mode"] != "local" and not values["endpoint"].strip():
            raise LayaClientError("laya_endpoint_missing",
                                  "远程、Jev 或 Workers AI 模式需要填写判断服务地址")
        if self.client_factory is None:
            raise LayaClientError("laya_client_unavailable", "Laya 执行适配器暂不可用，本轮继续普通回复流程")
        return self.client_factory(mode=values["mode"], endpoint=values["endpoint"],
                                   local_model_path=values["local_model_path"],
                                   api_key=values["api_key"], proxy=values.get("proxy", ""),
                                   timeout_seconds=values["timeout_seconds"],
                                   request_format=values.get("request_format", "native"),
                                   # An empty operator field must fall back to the backend's
                                   # own pinned model, which the client resolves from
                                   # MODE_MODELS; naming one here would override Jev and
                                   # Workers AI with the local checkpoint's name.
                                   model=values.get("remote_model", "").strip())

    async def test(self) -> dict:
        values = self.store.raw()
        started = perf_counter()
        test_values = {**values, "decision_fields": {name: True for name in values["decision_fields"]}}
        client = None
        try:
            client = self._client(values)
            result = await client.probe(enabled_fields=test_values["decision_fields"])
        except LayaClientError as error:
            self._record(test_values, "failed", round((perf_counter() - started) * 1000),
                         reason_code=error.code, reason=error.reason,
                         trace=getattr(client, "last_trace", None))
            raise
        decision = result.document()
        self._record(test_values, "ok", round((perf_counter() - started) * 1000), decision=decision,
                     trace=getattr(client, "last_trace", None))
        return {"status": "ok", "mode": values["mode"],
                "default_model": values["remote_model"] or MODE_MODELS[values["mode"]],
                "decision": decision}

    async def decide(self, facts: dict) -> dict:
        values = self.store.raw()
        if not values["enabled"]:
            self._record(values, "skipped", 0, reason_code="laya_disabled")
            return {"status": "skipped", "reason": "laya_disabled"}
        if not any(values["decision_fields"].values()):
            self._record(values, "skipped", 0, reason_code="laya_no_decision_fields")
            return {"status": "skipped", "reason": "laya_no_decision_fields"}
        started = perf_counter()
        client = None
        try:
            client = self._client(values)
            result = await client.decide(facts, enabled_fields=values["decision_fields"])
        except LayaClientError as error:
            if error.code in {"laya_initializing", "laya_gpu_unavailable"}:
                self._record(values, "skipped", round((perf_counter() - started) * 1000),
                             reason_code=error.code, reason=error.reason,
                             trace=getattr(client, "last_trace", None))
                return {"status": "skipped", "reason": error.code}
            if error.code == "laya_rule_skip":
                self._record(values, "skipped", round((perf_counter() - started) * 1000),
                             reason_code=error.code, reason=_safe_log_reason(error.code, error.reason),
                             trace=getattr(client, "last_trace", None))
                return {"status": "skipped", "reason": error.reason}
            self._record(values, "failed", round((perf_counter() - started) * 1000),
                         reason_code=error.code,
                         reason=_safe_log_reason(error.code, error.reason, api_key=str(values.get("api_key") or "")),
                         trace=getattr(client, "last_trace", None))
            raise
        except Exception as error:
            self._record(values, "failed", round((perf_counter() - started) * 1000),
                         reason_code=type(error).__name__, reason=_safe_log_reason(type(error).__name__, str(error)),
                         trace=getattr(client, "last_trace", None))
            raise
        decision, confidence_gate = _apply_followup_confidence_gate(
            result, facts, values["followup_confidence_threshold"])
        decision_document = decision.document()
        elapsed_ms = round((perf_counter() - started) * 1000)
        trace = getattr(client, "last_trace", None)
        if confidence_gate is not None and isinstance(trace, dict):
            trace = {**trace, "processing": dict(trace.get("processing") or {})}
            trace["processing"]["confidence_gate"] = confidence_gate
            trace["processing"]["model_decision"] = result.document()
            trace["processing"]["effective_decision"] = decision_document
        self._record(values, "ok", elapsed_ms, decision=decision_document, trace=trace)
        response = {"status": "ok", "decision": decision_document, "mode": values["mode"],
                    "elapsed_ms": elapsed_ms, "enabled_fields": dict(values["decision_fields"]),
                    "action_confidence": result.action_confidence}
        if confidence_gate is not None:
            response["confidence_gate"] = confidence_gate
        if confidence_gate is not None and confidence_gate["status"] == "below_threshold":
            response["reason"] = "laya_followup_confidence_below_threshold"
        return response

    async def classify_intervention(self, facts: dict) -> dict:
        """Use one bounded Laya question after the host has checked permission."""
        values = self.store.raw()
        if not values["enabled"]:
            self._record(values, "skipped", 0, reason_code="laya_disabled")
            return {"status": "skipped", "reason": "laya_disabled"}
        started = perf_counter()
        client = None
        try:
            client = self._client(values)
            choice = await client.classify_intervention(facts)
        except LayaClientError as error:
            if error.code in {"laya_initializing", "laya_gpu_unavailable"}:
                self._record(values, "skipped", round((perf_counter() - started) * 1000),
                    reason_code=error.code, reason=error.reason, trace=_intervention_trace(client))
                return {"status": "skipped", "reason": error.code}
            self._record(values, "failed", round((perf_counter() - started) * 1000),
                reason_code=error.code,
                reason=_safe_log_reason(error.code, error.reason, api_key=str(values.get("api_key") or "")),
                trace=_intervention_trace(client))
            raise
        except Exception as error:
            self._record(values, "failed", round((perf_counter() - started) * 1000),
                reason_code=type(error).__name__, reason=_safe_log_reason(type(error).__name__, str(error)),
                trace=_intervention_trace(client))
            raise
        elapsed_ms = round((perf_counter() - started) * 1000)
        result = {"intervention_need": choice}
        self._record(values, "ok", elapsed_ms, decision=result, trace=_intervention_trace(client))
        return {"status": "ok", **result, "mode": values["mode"], "elapsed_ms": elapsed_ms}

    def _observations_available(self) -> bool:
        try:
            rows = self.runtime.read_all(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'astrbot_core_reply_observations'",
                (),
            )
            return bool(rows)
        except Exception:
            return False

    def _resolve_scopes_from_observations(self, *, batch_ids, origin_keys):
        by_batch: dict[str, dict] = {}
        by_origin: dict[str, dict] = {}
        clean_batches = sorted({b for b in batch_ids if isinstance(b, str) and b.strip()})
        clean_origins = sorted({o for o in origin_keys if isinstance(o, str) and o.strip()})
        if (not clean_batches and not clean_origins) or not self._observations_available():
            return by_batch, by_origin
        for start in range(0, len(clean_batches), _OBSERVATION_BATCH_CHUNK):
            chunk = clean_batches[start:start + _OBSERVATION_BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            obs_rows = self.runtime.read_all(
                f"SELECT turn_id, context_json FROM astrbot_core_reply_observations WHERE turn_id IN ({placeholders})",
                tuple(chunk),
            )
            for row in obs_rows:
                parsed = _parse_observation_scope(row["context_json"])
                if parsed:
                    by_batch[row["turn_id"]] = parsed
        for start in range(0, len(clean_origins), _OBSERVATION_BATCH_CHUNK):
            chunk = clean_origins[start:start + _OBSERVATION_BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            obs_rows = self.runtime.read_all(
                f"SELECT context_key, context_json FROM astrbot_core_reply_observations "
                f"WHERE context_key IN ({placeholders}) GROUP BY context_key",
                tuple(chunk),
            )
            for row in obs_rows:
                parsed = _parse_observation_scope(row["context_json"])
                if parsed:
                    by_origin[row["context_key"]] = parsed
        return by_batch, by_origin

    @staticmethod
    def _processing_scope(processing_raw) -> dict | None:
        if isinstance(processing_raw, dict):
            scope = processing_raw.get("scope")
            return scope if isinstance(scope, dict) else None
        if not isinstance(processing_raw, str) or '"scope"' not in processing_raw:
            return None
        try:
            parsed = json.loads(processing_raw)
        except (TypeError, ValueError):
            return None
        scope = parsed.get("scope") if isinstance(parsed, dict) else None
        return scope if isinstance(scope, dict) else None

    def _materialize_events(self, rows, by_batch, by_origin):
        events = []
        for row in rows:
            item = dict(row)
            origin_key = item.pop("origin_key", None)
            for source, target in (("decision_json", "decision"), ("enabled_fields_json", "enabled_fields"),
                                   ("request_json", "request"), ("response_json", "response"),
                                   ("processing_json", "processing")):
                try:
                    item[target] = json.loads(item.pop(source))
                except (TypeError, ValueError):
                    item[target] = None if target in {"decision", "request", "response"} else {}
            proc_scope = self._processing_scope(item.get("processing"))
            obs_scope = (by_batch.get(item.get("batch_id"))
                         or by_origin.get(origin_key)
                         or (self._scope_meta if origin_key == self.origin_key else None))
            item["scope_info"] = _public_scope_info(
                proc_scope or obs_scope,
                request_doc=item.get("request"),
                source=item.get("source"),
            )
            events.append(item)
        return events

    def recent(self, limit=50, *, all_origins: bool = False, scope_key: str | None = None) -> dict:
        if self.runtime is None:
            return {"events": []}
        limit = max(0, int(limit))
        if limit == 0:
            return {"events": []}
        if scope_key is not None:
            target_scope = str(scope_key).strip()
            if not target_scope:
                return {"events": []}
            candidates = self.runtime.read_all(
                "SELECT event_id, origin_key, batch_id, processing_json "
                "FROM laya_decision_events ORDER BY observed_at DESC, rowid DESC",
                (),
            )
            unresolved_batches = set()
            unresolved_origins = set()
            pre_scopes = []
            for row in candidates:
                proc_scope = self._processing_scope(row["processing_json"])
                pre_scopes.append(proc_scope)
                if proc_scope is None:
                    if row["batch_id"]:
                        unresolved_batches.add(row["batch_id"])
                    if row["origin_key"]:
                        unresolved_origins.add(row["origin_key"])
            by_batch, by_origin = self._resolve_scopes_from_observations(
                batch_ids=unresolved_batches, origin_keys=unresolved_origins)
            matched_ids = []
            for row, proc_scope in zip(candidates, pre_scopes):
                resolved = proc_scope or by_batch.get(row["batch_id"]) or by_origin.get(row["origin_key"])
                if resolved and resolved.get("scope_key") == target_scope:
                    matched_ids.append(row["event_id"])
                    if len(matched_ids) >= limit:
                        break
            if not matched_ids:
                return {"events": []}
            rows = []
            for start in range(0, len(matched_ids), _OBSERVATION_BATCH_CHUNK):
                chunk = matched_ids[start:start + _OBSERVATION_BATCH_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows.extend(self.runtime.read_all(
                    f"{_SELECT_EVENT_COLUMNS} WHERE event_id IN ({placeholders}) "
                    "ORDER BY observed_at DESC, rowid DESC",
                    tuple(chunk),
                ))
            order = {event_id: idx for idx, event_id in enumerate(matched_ids)}
            rows.sort(key=lambda row: order.get(row["event_id"], len(matched_ids)))
            return {"events": self._materialize_events(rows, by_batch, by_origin)}

        if all_origins:
            rows = self.runtime.read_all(
                f"{_SELECT_EVENT_COLUMNS} ORDER BY observed_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        else:
            if self.origin_key is None:
                return {"events": []}
            rows = self.runtime.read_all(
                f"{_SELECT_EVENT_COLUMNS} WHERE origin_key = ? "
                "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
                (self.origin_key, limit),
            )
        unresolved_batches = set()
        unresolved_origins = set()
        for row in rows:
            if self._processing_scope(row["processing_json"]) is None:
                if row["batch_id"]:
                    unresolved_batches.add(row["batch_id"])
                if row["origin_key"]:
                    unresolved_origins.add(row["origin_key"])
        by_batch, by_origin = self._resolve_scopes_from_observations(
            batch_ids=unresolved_batches, origin_keys=unresolved_origins)
        return {"events": self._materialize_events(rows, by_batch, by_origin)}

    def _record(self, values, status, elapsed_ms, *, reason_code=None, reason=None, decision=None, trace=None):
        if self.runtime is None or self.origin_key is None:
            return
        try:
            # 这两列的宽度由 laya_decision_events 的 CHECK 定死：超限的 JSON 会被库
            # 直接拒绝，整条台账连同一句「写入失败」一起消失。改成带上 truncated 标记
            # 的预览后，行还在，读者也知道自己看到的是被截断的文档。
            decision_json = _diagnostic_json(decision, 4096) if decision else None
            enabled_fields_json = _diagnostic_json(values["decision_fields"], 2048, sort_keys=True)
            safe_reason = (_safe_log_reason(reason_code, reason, api_key=str(values.get("api_key") or ""))
                           if reason_code else None)
            trace = trace if isinstance(trace, dict) else {}
            processing = dict(trace.get("processing") or {})
            processing.update({"status": status, "reason_code": reason_code})
            if self._scope_meta and "scope" not in processing:
                processing["scope"] = dict(self._scope_meta)
            if safe_reason:
                processing["reason"] = safe_reason
            request_json = _diagnostic_json(trace.get("request"), 131072)
            response_json = _diagnostic_json(trace.get("response"), 65536)
            processing_json = _diagnostic_json(processing, 16384)
            with self.runtime.transaction() as connection:
                event_id = uuid4().hex
                connection.execute("""INSERT INTO laya_decision_events
                    (event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
                     decision_json, enabled_fields_json, request_json, response_json, processing_json, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, self.origin_key, self.event_ref, self.source, status, _ledger_mode(values["mode"]),
                     str(reason_code)[:96] if reason_code else None, str(safe_reason)[:1000] if safe_reason else None,
                     elapsed_ms, decision_json, enabled_fields_json, request_json, response_json, processing_json,
                     datetime.now(timezone.utc).isoformat()))
                connection.execute("""DELETE FROM laya_decision_events WHERE event_id IN (
                    SELECT event_id FROM laya_decision_events WHERE origin_key = ?
                    ORDER BY observed_at DESC, rowid DESC LIMIT -1 OFFSET 2000)""", (self.origin_key,))
            self._last_recorded_event_id = event_id
        except Exception as error:
            logger.warning("Laya 决策记录写入失败 code=%s detail=%s",
                           type(error).__name__, _write_failure_detail(error))
