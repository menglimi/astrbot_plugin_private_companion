import asyncio
import json

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.news_exploration import NewsExplorationMixin


class _ShareHarness(NewsExplorationMixin, ProactiveMessageMixin):
    pass


class _NewsHarness(NewsExplorationMixin, ProactiveMessageMixin):
    @staticmethod
    def _parse_json_object(raw):
        return json.loads(raw)


# --- external_share_require_source_link ---


def test_require_source_link_false_allows_mention_only():
    harness = _ShareHarness()
    harness.external_share_require_source_link = False
    user = {
        "news_context": {
            "headline": "Anthropic 给 Claude 文字加水印",
            "selected_link": "https://daringfireball.net/2026/08/x",
        }
    }
    decision = harness._external_share_source_consistency_decision(
        user,
        "刚看到 Anthropic 给 Claude 文字加水印的新闻，挺有讨论的。",
        reason="news_share",
    )
    assert decision is None  # 正文提到真实标题即通过，不强制带 URL


def test_require_source_link_default_still_requires_url():
    harness = _ShareHarness()  # 默认 True = 现状行为
    user = {
        "news_context": {
            "headline": "Anthropic 给 Claude 文字加水印",
            "selected_link": "https://daringfireball.net/2026/08/x",
        }
    }
    decision = harness._external_share_source_consistency_decision(
        user,
        "刚看到 Anthropic 给 Claude 文字加水印的新闻，挺有讨论的。",
        reason="news_share",
    )
    assert decision is not None
    assert decision["decision"] == "rewrite"
    assert "遗漏真实来源链接" in decision["reason"]


# --- anchor 前缀 → 兜底抓真实标题 ---


def test_anchor_text_prefixes_real_fields():
    harness = _ShareHarness()
    user = {
        "news_context": {
            "headline": "Anthropic 给 Claude 文字加水印",
            "selected_link": "https://daringfireball.net/x",
        }
    }
    anchor = harness._external_share_anchor_text(user, reason="news_share", topic="一个轻松的早安小趣闻")
    assert "标题：Anthropic 给 Claude 文字加水印" in anchor
    assert "链接：https://daringfireball.net/x" in anchor
    # 泛化 topic 仍是第一分句，但带前缀的真实标题行可被 fallback pattern-1 命中
    assert anchor.startswith("一个轻松的早安小趣闻")


def test_fallback_grabs_prefixed_real_title_over_generic_first_segment():
    harness = _ShareHarness()
    source = (
        "一个轻松的早安小趣闻/暖心短句；"
        "标题：Anthropic 给 Claude 文字加水印；"
        "链接：https://daringfireball.net/2026/08/anthropics_watermark"
    )
    text = harness._external_share_fallback_reference(source)
    assert "Anthropic" in text
    assert "早安" not in text  # 泛化 topic 不再被当成标题
    assert "daringfireball.net" in text


# --- URL 不被句子化管线破坏 ---


def test_normalize_flow_preserves_url_integrity():
    harness = _ShareHarness()
    url = "https://daringfireball.net/2026/08/anthropics_watermark_text"
    out = harness._normalize_proactive_sentence_flow(f"看看这个热闹～ {url}")
    assert url in out


# --- 统一评分 total 阈值参数化 ---


def test_share_decision_min_total_configurable():
    harness = _ShareHarness()
    harness.data = {}
    harness.idle_minutes = 40
    harness.min_interval_minutes = 120
    harness.external_event_share_min_total = 0
    decision = harness._external_event_share_decision(
        {},
        {"selected_link": "http://example.com/a"},
        source_type="news",
        wish={"should_share": True, "relevance": 1, "desire": 1},
        now=0,
    )
    assert decision["should_share"] is True


# --- 意愿 override（rel/des 阈值） ---


def test_override_flips_should_share_when_thresholds_met(monkeypatch):
    harness = _NewsHarness()
    harness.data = {}
    harness.enable_external_event_self_link = True
    harness.external_event_self_link_override_min_relevance = 5
    harness.external_event_self_link_override_min_desire = 5
    harness.external_event_self_link_override_probability = 0.7
    monkeypatch.setattr(harness, "_external_event_self_link_provider_id", lambda *a, **k: "deepseek/test")
    monkeypatch.setattr(harness, "_format_external_event_stable_self_context", lambda *a, **k: "")
    monkeypatch.setattr(harness, "_format_external_event_current_self_context", lambda *a, **k: "")
    monkeypatch.setattr(harness, "_external_event_life_opportunity_wish", lambda *a, **k: None)

    async def fake_llm_call(*args, **kwargs):
        return (
            '{"relevance":5,"desire":5,"should_share":false,"share_probability":0.1,'
            '"self_link":"和自己相关","motive":"想说说","tone":"自然","boundary":"别像通知"}'
        )

    monkeypatch.setattr(harness, "_llm_call", fake_llm_call, raising=False)
    payload = {"headline": "某条普通科技新闻", "impression": "普通内容，不含生活福利词"}
    result = asyncio.run(harness._build_external_event_wish(payload, source_type="news"))
    assert result["should_share"] is True
    assert result["boost_reason"] == "override_by_user_threshold"
    assert result["share_probability"] >= 0.7


def test_override_not_applied_when_thresholds_zero(monkeypatch):
    harness = _NewsHarness()
    harness.data = {}
    harness.enable_external_event_self_link = True
    # override 阈值默认 0 = 不启用放宽，保持 LLM 原判
    monkeypatch.setattr(harness, "_external_event_self_link_provider_id", lambda *a, **k: "deepseek/test")
    monkeypatch.setattr(harness, "_format_external_event_stable_self_context", lambda *a, **k: "")
    monkeypatch.setattr(harness, "_format_external_event_current_self_context", lambda *a, **k: "")
    monkeypatch.setattr(harness, "_external_event_life_opportunity_wish", lambda *a, **k: None)

    async def fake_llm_call(*args, **kwargs):
        return (
            '{"relevance":5,"desire":5,"should_share":false,"share_probability":0.1,'
            '"self_link":"和自己相关","motive":"想说说","tone":"自然","boundary":"别像通知"}'
        )

    monkeypatch.setattr(harness, "_llm_call", fake_llm_call, raising=False)
    payload = {"headline": "另一条普通科技新闻", "impression": "普通内容"}
    result = asyncio.run(harness._build_external_event_wish(payload, source_type="news"))
    assert result["should_share"] is False
    assert "boost_reason" not in result
