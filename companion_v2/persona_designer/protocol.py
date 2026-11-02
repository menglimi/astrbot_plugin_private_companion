"""Bounded documents for a client-owned, explicitly saved design conversation."""

from __future__ import annotations

import json
from pathlib import Path

from companion_v2.execution.models import TaskError


STAGES = ("positioning", "personality", "voice", "background", "review")
TEXT_LIMITS = {"provider_id": 200, "source_persona_id": 200, "source_text": 30000,
               "confirmed": 16000, "draft": 65536, "lorebook": 20000, "input": 8000}
MAX_DOCUMENT_BYTES = 768000


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


def model_response(raw):
    if type(raw) is not str or not raw.strip() or len(raw) > 140000:
        raise TaskError("persona_designer_response_invalid", "模型没有返回完整的设计结果，可以重试本轮。")
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate result key")
            result[key] = value
        return result

    try:
        result = json.loads(text, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
        if type(result) is not dict or set(result) != {"reply", "options", "confirmed", "stage", "draft", "lorebook"}:
            raise ValueError("invalid response fields")
        for key, limit in {"reply": 16000, "confirmed": 16000, "draft": 65536, "lorebook": 20000}.items():
            if type(result[key]) is not str or len(result[key]) > limit:
                raise ValueError("invalid response text")
        if not result["reply"].strip() or result["stage"] not in STAGES:
            raise ValueError("missing reply or stage")
        if (type(result["options"]) is not list or len(result["options"]) > 3
                or any(type(value) is not str or not value.strip() or len(value) > 300
                       for value in result["options"])):
            raise ValueError("invalid options")
        return result
    except (ValueError, TypeError, KeyError) as error:
        raise TaskError("persona_designer_response_invalid", "模型返回的设计结果不完整，可以重试本轮。") from error
