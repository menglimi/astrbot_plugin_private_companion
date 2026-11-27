"""Bounded documents for a client-owned, explicitly saved design conversation."""

from __future__ import annotations

import json
from pathlib import Path
import re

from companion_v2.execution.models import TaskError


STAGES = ("positioning", "personality", "voice", "background", "review")
TEXT_LIMITS = {"provider_id": 200, "source_persona_id": 200, "source_text": 30000,
               "confirmed": 16000, "draft": 65536, "lorebook": 20000, "input": 8000}
MAX_DOCUMENT_BYTES = 768000
_FENCE_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_RESPONSE_KEYS = frozenset({"reply", "options", "confirmed", "stage", "draft", "lorebook"})


MAX_JSON_DECODE_ATTEMPTS = 256


def _decode_response_object(raw: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate result key")
            result[key] = value
        return result

    def reject_nonfinite(_token):
        raise ValueError("invalid number")

    decoder = json.JSONDecoder(object_pairs_hook=unique, parse_constant=reject_nonfinite)
    text = raw.strip()
    search_spaces = []
    for match in _FENCE_BLOCK.finditer(text):
        inner = match.group(1).strip()
        if inner:
            search_spaces.append(inner)
    search_spaces.append(text)

    seen_spaces = set()
    last_error = None
    for space in search_spaces:
        if space in seen_spaces:
            continue
        seen_spaces.add(space)
        pos = 0
        attempts = 0
        while attempts < MAX_JSON_DECODE_ATTEMPTS:
            start = space.find("{", pos)
            if start < 0:
                break
            attempts += 1
            try:
                parsed, _end = decoder.raw_decode(space, start)
            except json.JSONDecodeError as error:
                last_error = error
                pos = start + 1
                continue
            if type(parsed) is dict:
                return parsed
            last_error = ValueError("response is not a JSON object")
            pos = start + 1
    if last_error is not None:
        raise last_error
    raise ValueError("no JSON object found")


def invalid():
    return TaskError("persona_designer_invalid", "设计内容过长或格式不完整，请检查后重试。")


def document(raw):
    """Copy only the declared, text-only design state; retain source text verbatim."""
    if type(raw) is not dict or set(raw) - {*TEXT_LIMITS, "mode", "stage", "messages", "options"}:
        raise invalid()
    if (type(raw.get("mode")) is not str or raw["mode"] not in {"optimize", "create"}
            or raw.get("stage", "positioning") not in STAGES):
        raise invalid()
    result = {"mode": raw["mode"], "stage": raw.get("stage", "positioning")}
    for key, limit in TEXT_LIMITS.items():
        value = raw.get(key, "")
        if type(value) is not str or len(value) > limit:
            raise invalid()
        result[key] = value
    messages = raw.get("messages", [])
    if (type(messages) is not list or len(messages) > 100
            or any(type(item) is not dict or set(item) != {"role", "content"}
                   or type(item["role"]) is not str or item["role"] not in {"user", "assistant"}
                   or type(item["content"]) is not str or len(item["content"]) > 16000
                   for item in messages)):
        raise invalid()
    result["messages"] = [dict(item) for item in messages]
    options = raw.get("options", [])
    if (type(options) is not list or len(options) > 3
            or any(type(item) is not str or len(item) > 300 for item in options)):
        raise invalid()
    result["options"] = list(options)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise invalid()
    return result


def model_request(state, action):
    state = document(state)
    if type(action) is not str or action not in {"chat", "draft", "tryout"} or not state["provider_id"].strip():
        raise invalid()
    if state["mode"] == "optimize" and not state["source_text"].strip():
        raise TaskError("persona_designer_source_required", "请先输入人设，或从「选择人设」载入一份。")
    if action == "chat" and (not state["messages"] or state["messages"][-1]["role"] != "user"):
        raise invalid()
    if action == "tryout" and not state["draft"].strip():
        raise TaskError("persona_designer_draft_required", "先生成或填写一份人设稿，再试试它的口吻。")
    payload = {key: value for key, value in state.items() if key != "input"}
    payload["action"] = action
    if len(json.dumps(payload, ensure_ascii=False)) > 140000:
        raise TaskError("persona_designer_context_full", "这次设计的内容较长，请保存草稿并精简当前对话后继续。")
    method = Path(__file__).with_name("methodology.md").read_text(encoding="utf-8")
    return method, json.dumps(payload, ensure_ascii=False)


def model_response(raw, *, default_stage="positioning", fallback_confirmed="",
                   fallback_draft="", fallback_lorebook=""):
    if type(raw) is not str or not raw.strip() or len(raw) > 140000:
        raise TaskError("persona_designer_response_invalid", "模型没有返回完整的设计结果，可以重试本轮。")
    try:
        result = _decode_response_object(raw)
        if type(result) is not dict or set(result) - _RESPONSE_KEYS:
            raise ValueError("invalid response fields")
        reply = result.get("reply")
        if type(reply) is not str or not reply.strip() or len(reply) > 16000:
            raise ValueError("missing or invalid reply")
        stage = result.get("stage")
        if stage is None and "stage" not in result:
            stage = default_stage
        if type(stage) is not str or stage not in STAGES:
            raise ValueError("missing or invalid stage")
        defaults = {
            "confirmed": fallback_confirmed,
            "draft": fallback_draft,
            "lorebook": fallback_lorebook,
        }
        normalized = {"reply": reply, "stage": stage}
        for key, limit in {"confirmed": 16000, "draft": 65536, "lorebook": 20000}.items():
            if key not in result or result[key] is None:
                value = defaults[key]
            else:
                value = result[key]
            if type(value) is not str or len(value) > limit:
                raise ValueError("invalid response text")
            normalized[key] = value
        raw_options = result.get("options") if "options" in result and result["options"] is not None else []
        if type(raw_options) is not list:
            raise ValueError("invalid options")
        options = []
        for value in raw_options:
            if type(value) is not str or len(value) > 300:
                raise ValueError("invalid options")
            cleaned = value.strip()
            if cleaned:
                options.append(cleaned)
        normalized["options"] = options[:3]
        return normalized
    except (ValueError, TypeError, KeyError) as error:
        raise TaskError("persona_designer_response_invalid", "模型返回的设计结果不完整，可以重试本轮。") from error
