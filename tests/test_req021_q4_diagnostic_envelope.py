from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

from tests.module_source_index import find_methods, file_family_source_text, page_api_source_text


ROOT = Path(__file__).resolve().parents[1]
PAGE_API_HOST = ROOT / "page_api.py"
PAGE_API_CLASS = "PrivateCompanionPageApi"
MODULE_PATH = ROOT / "diagnostic_envelope.py"


def _module():
    spec = importlib.util.spec_from_file_location("q4_diagnostic_envelope", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_q4_envelope_adds_public_fields_and_keeps_legacy_compatibility() -> None:
    api = _module()
    result = api.normalize_diagnostic_result(
        {"ok": True, "type": "tts_generation", "elapsed_ms": 42, "title": "TTS test", "sample": "normal"},
        test_id="diag_tts_generation_012345abcdef",
    )

    assert result["diagnostic_version"] == api.DIAGNOSTIC_ENVELOPE_VERSION
    assert result["test_id"] == "diag_tts_generation_012345abcdef"
    assert result["duration_ms"] == result["elapsed_ms"] == 42
    assert result["phase"] == "completed"
    assert result["error_category"] == "none"
    assert result["retryable"] is False
    assert result["next_step"]
    assert result["ok"] is True
    assert result["sample"] == ""
    assert result["path"] == ""


def test_q4_envelope_removes_secrets_paths_and_conversation_content() -> None:
    api = _module()
    secret = "q4-secret-token"
    raw = {
        "ok": False,
        "type": "image_api_endpoint",
        "title": "a private title should not leave the diagnostic boundary",
        "error": f"Traceback Authorization: Bearer {secret} at C:\\Users\\tester\\photo.png",
        "endpoint_url": f"https://images.example.test/v1/generate?api_key={secret}",
        "path": "C:\\Users\\tester\\photo.png",
        "prompt_path": "/var/lib/astrbot/private/prompt.json",
        "reference_image": "/var/lib/astrbot/private/reference.jpg",
        "prompt": "chat secret should not leave the diagnostic boundary",
        "text_preview": "a private screen/chat excerpt",
        "original_text_preview": "another private chat excerpt",
        "final_text_preview": "one more private chat excerpt",
        "sample": "model response should not be echoed",
        "steps": [{"name": "Provider call", "status": "error", "detail": secret}],
    }
    result = api.normalize_diagnostic_result(raw, test_id="diag_image_api_endpoint_012345abcdef")
    rendered = json.dumps(result, ensure_ascii=False)

    assert secret not in rendered
    assert "C:\\Users" not in rendered
    assert "/var/lib/astrbot" not in rendered
    assert "private screen/chat" not in rendered
    assert "private title" not in rendered
    assert result["endpoint_url"] == "https://images.example.test/v1/generate"
    assert result["error_category"] == "authorization"
    assert result["error"] == "Authentication or permission was not accepted."
    assert result["steps"] == [{"name": "Provider call", "status": "error", "detail": "This stage did not pass; see the next step below."}]


def test_q4_error_categories_retryability_and_safe_ids() -> None:
    api = _module()
    cases = {
        "missing model configuration": ("configuration", False),
        "401 unauthorized": ("authorization", False),
        "request timed out": ("timeout", True),
        "service not available": ("unavailable", True),
        "invalid test parameter": ("validation", False),
        "provider rate limit 429": ("provider", True),
    }
    for message, expected in cases.items():
        result = api.normalize_diagnostic_result({"ok": False, "type": "provider", "error": message})
        assert (result["error_category"], result["retryable"]) == expected
        assert result["next_step"]

    test_id = api.diagnostic_test_id("provider / C:\\private / user 10001", token="012345abcdef")
    assert re.fullmatch(r"diag_[a-z0-9_]+_[a-f0-9]{12}", test_id)
    assert "10001" not in test_id
    assert "private" not in test_id


def test_q4_legacy_history_is_projected_without_sensitive_fields() -> None:
    api = _module()
    result = api.normalize_diagnostic_result(
        {"ok": False, "type": "tts_generation", "elapsed_ms": 9, "umo": "aiocqhttp:FriendMessage:10001"}
    )
    assert result["test_id"] == "diag_tts_generation_legacy"
    assert result["umo"] == ""
    assert result["elapsed_ms"] == result["duration_ms"] == 9


def test_q4_page_api_and_operations_contract_use_the_same_envelope() -> None:
    page_api = page_api_source_text(ROOT)
    # 拆分后方法可能挪到 integration_status_part*.py，聚合整族查找
    integration_status = file_family_source_text(ROOT, "integration_status.py")
    app_js = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
    provider_panel = (ROOT / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js").read_text(encoding="utf-8")
    # 方法可能被拆到 page_api_*.py 的域 mixin 里，按宿主类全族定位。
    located = find_methods(
        ROOT,
        "page_api",
        PAGE_API_CLASS,
        ("run_troubleshooting_test", "test_image_api_endpoint", "test_provider"),
    )
    methods = {name: ast.unparse(node) for name, node in located.items()}

    for name in ("run_troubleshooting_test", "test_image_api_endpoint", "test_provider"):
        assert "_diagnostic_envelope" in methods[name]
        assert "diagnostic_test_id" in methods[name]
    assert "def _sanitize_troubleshooting_test_result" in page_api
    assert "return self._diagnostic_envelope(result)" in page_api
    assert "def _diagnostic_operations_contract" in integration_status
    assert "DIAGNOSTIC_ENVELOPE_VERSION" in integration_status
    assert "function troubleshootingDiagnosticEnvelopeMarkup" in app_js
    assert "result.next_step || result.error" in provider_panel
