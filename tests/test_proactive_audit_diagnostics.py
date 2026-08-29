# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


ROOT = Path(__file__).resolve().parents[1]


class _AuditHarness(ProactiveEngineMixin):
    def __init__(self) -> None:
        self.data = {"proactive_audit_log": []}
        self.config = {}

    def _planned_proactive_semantics(self, _user):
        return {}

    def _framework_agent_meta_summary_leak(self, _text: str) -> bool:
        return False


class ProactiveAuditDiagnosticTests(unittest.TestCase):
    def test_audit_keeps_redacted_diagnostic_and_marks_summary_truncation(self) -> None:
        harness = _AuditHarness()
        user = {
            "user_id": "10001",
            "planned_proactive_source": "troubleshooting",
            "planned_proactive_reason": "check_in",
            "planned_proactive_action": "message",
        }
        audit_id = harness._append_proactive_audit(
            "10001",
            user,
            status="running",
            note="started",
        )
        diagnostic = (
            "RuntimeError: 主动消息发送失败: umo=default:FriendMessage:10001 platform=qq; "
            "precise=session unavailable; fallback=core rejected; direct=retcode 1200; "
            "api_key=sk-tests" "ecret123456"
        )

        harness._update_proactive_audit(
            audit_id,
            status="failed",
            note="发送失败：" + ("详细错误" * 80),
            diagnostic_detail=diagnostic,
        )

        item = harness.data["proactive_audit_log"][0]
        self.assertTrue(item["note"].endswith("…"))
        self.assertLessEqual(len(item["note"]), 180)
        self.assertIn("precise=session unavailable", item["diagnostic_detail"])
        self.assertIn("direct=retcode 1200", item["diagnostic_detail"])
        self.assertIn("[密钥已隐藏]", item["diagnostic_detail"])
        self.assertNotIn("sk-testsecret123456", item["diagnostic_detail"])

    def test_troubleshooting_payload_preserves_expanded_diagnostic(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        diagnostic = "fallback=" + ("发送接口返回失败；" * 80)

        result = api._sanitize_troubleshooting_test_result(
            {
                "type": "proactive_message",
                "ok": False,
                "error": "发送失败",
                "diagnostic_detail": diagnostic,
            }
        )

        self.assertEqual(result["diagnostic_detail"], diagnostic)
        self.assertGreater(len(result["diagnostic_detail"]), 220)

    def test_frontend_uses_expandable_structured_diagnostic(self) -> None:
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")

        self.assertIn("function proactiveDiagnosticRows", script)
        self.assertIn("查看完整发送诊断", script)
        self.assertIn("这条旧记录只保存了错误摘要", script)
        self.assertIn("精确会话发送", script)
        self.assertIn("AstrBot 核心发送", script)
        self.assertIn("proactive-audit-line", script)
        self.assertIn(".proactive-diagnostic", styles)
        self.assertIn("@media (max-width: 680px)", styles)


if __name__ == "__main__":
    unittest.main()
