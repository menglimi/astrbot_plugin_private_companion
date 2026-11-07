"""每日反思提案的归一化：一个措辞不该让整条事实变成 invalid_proposal。

现场（2026-11-04 的库）：模型在 ``details`` 里写 ``attribution=self_report`` /
``self`` / ``direct_observation``，而内部合同只认 user / third_party / bot /
unknown / observed。``context_mode`` / ``polarity`` / ``retention`` 早就有同一层
归一化，``attribution`` 是漏掉的那一个——于是整条提案被拒，事实一条都没写进去。
"""
import unittest

from companion_v2.memory.reflection_output import normalize_proposal

ALLOWED = {
    "attribution": {"user", "third_party", "bot", "unknown", "observed"},
    "context_mode": {"relevant", "pinned"},
    "polarity": {"positive", "negative", "uncertain"},
    "retention": {"session", "short", "durable", "protected"},
}


def proposal(**details):
    """复刻现场那种把语义塞进 details 的提案形状。"""
    return {
        "operation": "add",
        "predicate": "观影情况",
        "object": "《年会不能停2》尚未观看",
        "representation": "用户自述一直没看。",
        "evidence_handles": ["s19"],
        "details": [{"field": key, "value": value} for key, value in details.items()],
    }


class ProposalAttributionTests(unittest.TestCase):
    def test_soft_attribution_aliases_fold_into_the_contract_values(self):
        cases = {"self": "user", "self_report": "user", "用户自述": "user",
                 "direct_observation": "observed", "third-party": "third_party",
                 "角色": "bot", "不确定": "unknown"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                data = normalize_proposal(proposal(attribution=raw), {})
                self.assertEqual(data["attribution"], expected)

    def test_unknown_attribution_is_dropped_instead_of_rejecting_the_fact(self):
        data = normalize_proposal(proposal(attribution="我猜的"), {})
        self.assertNotIn("attribution", data, "认不出的归因应回落到默认值 user，而不是拒绝整条提案")
        for field, allowed in ALLOWED.items():
            if field in data:
                self.assertIn(data[field], allowed, field)

    def test_mixed_soft_details_stay_inside_the_contract(self):
        """现场那条被拒的提案：归因、自由文本 context_mode、数字与限定一起出现。"""
        data = normalize_proposal(proposal(
            subject="self", attribution="self_report", confidence=0.75,
            stability_estimate="low", context_mode="casual_chat",
            qualifiers=[{"key": "时间", "value": "截至2026-10-31"}],
        ), {})
        self.assertEqual(data["attribution"], "user")
        self.assertNotIn("context_mode", data)
        self.assertEqual(data["qualifiers"], {"时间": "截至2026-10-31"})
        self.assertEqual(data["subject"], "self")
        self.assertEqual(data["confidence"], 0.75)


if __name__ == "__main__":
    unittest.main()
