import asyncio

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _ShareHarness(ProactiveMessageMixin):
    pass


class _WrongPersonaRewriteHarness(_ShareHarness):
    @staticmethod
    def _private_user_role(*args, **kwargs):
        return "friend"

    async def _rewrite_reference_reply_with_persona(self, *args, **kwargs):
        return "刚刷到 B站《崩坏：星穹铁道》，这个标题有点想丢给你看一眼。 https://www.solidot.org/story"

    @staticmethod
    def _strip_internal_identity_anchors(text):
        return text


def test_source_platform_follows_url_domain():
    assert _ShareHarness._external_share_platform_from_url("https://www.douyin.com/note/123") == "抖音"
    assert _ShareHarness._external_share_platform_from_url("https://www.bilibili.com/video/BV1234567890") == "B站"
    assert _ShareHarness._external_share_platform_from_url("https://example.com/post/1") == ""
    assert _ShareHarness._external_share_platform_from_url("https://notbilibili.com/video/BV1234567890") == ""
    assert _ShareHarness._external_share_platform_from_url("https://bilibili.com.example.com/video/BV1234567890") == ""


def test_web_share_does_not_mix_stale_bilibili_context():
    harness = _ShareHarness()
    user = {
        "bilibili_video_context": {"title": "旧的 B站视频", "bvid": "BV1234567890"},
        "web_exploration_context": {
            "source_title": "外面的雨雪停了",
            "source_url": "https://www.douyin.com/note/7525700842172534025",
        },
    }

    anchor = harness._external_share_anchor_text(user, reason="web_exploration_share")

    assert "外面的雨雪停了" in anchor
    assert "douyin.com" in anchor
    assert "旧的 B站视频" not in anchor
    assert "BV1234567890" not in anchor


def test_wrong_platform_claim_is_rewritten_as_one_natural_reference():
    harness = _ShareHarness()
    user = {
        "web_exploration_context": {
            "source_title": "外面的雨雪停了",
            "source_url": "https://www.douyin.com/note/7525700842172534025",
            "note": "标题让人想停下来看看",
        }
    }

    decision = harness._external_share_source_consistency_decision(
        user,
        "刚刷到 B站《外面的雨雪停了》，想给你看看。",
        reason="web_exploration_share",
    )

    assert decision is not None
    assert decision["decision"] == "rewrite"
    assert "text" not in decision
    assert "抖音" in decision["reference_text"]
    assert "B站" not in decision["reference_text"]
    assert "douyin.com" in decision["reference_text"]


def test_generic_web_link_cannot_be_claimed_as_bilibili():
    harness = _ShareHarness()
    user = {
        "news_context": {
            "topic": "蒋方舟因论文存在抄袭行为被撤销硕士学位",
            "selected_link": "https://www.solidot.org/story",
        }
    }

    decision = harness._external_share_source_consistency_decision(
        user,
        "刚刷到 B站《崩坏：星穹铁道》，这个标题想给你看看。 https://www.solidot.org/story",
        reason="news_share",
    )

    assert decision is not None
    assert decision["decision"] == "rewrite"
    assert "B站" not in decision["reference_text"]
    assert "蒋方舟" in decision["reference_text"]
    assert "solidot.org" in decision["reference_text"]


def test_persona_text_cannot_drop_the_real_source_link():
    harness = _ShareHarness()
    user = {
        "web_exploration_context": {
            "source_title": "外面的雨雪停了",
            "source_url": "https://www.douyin.com/note/7525700842172534025",
        }
    }

    decision = harness._external_share_source_consistency_decision(
        user,
        "刚在抖音刷到‘外面的雨雪停了’，这个标题让我停了一下。",
        reason="web_exploration_share",
    )

    assert decision is not None
    assert decision["decision"] == "rewrite"
    assert "遗漏真实来源链接" in decision["reason"]
    assert "douyin.com" in decision["reference_text"]


def test_persona_rewrite_cannot_reintroduce_wrong_platform_claim():
    harness = _WrongPersonaRewriteHarness()
    user = {
        "nickname": "测试用户",
        "news_context": {
            "topic": "蒋方舟因论文存在抄袭行为被撤销硕士学位",
            "selected_link": "https://www.solidot.org/story",
        },
    }

    decision = asyncio.run(
        harness._review_proactive_message_send_decision(
            user,
            "刚刷到 B站《崩坏：星穹铁道》，这个标题有点想丢给你看一眼。 https://www.solidot.org/story",
            reason="news_share",
            action="message",
            topic="蒋方舟因论文存在抄袭行为被撤销硕士学位",
            motive="刚看到一条新闻，想自然分享",
        )
    )

    assert decision["decision"] == "rewrite"
    assert "B站" not in decision["text"]
    assert "崩坏：星穹铁道" not in decision["text"]
    assert "蒋方舟" in decision["text"]
    assert "solidot.org" in decision["text"]


def test_fallback_prefers_reference_title_over_internal_preamble():
    harness = _ShareHarness()
    source = (
        "网页探索线索；"
        "参考来源：外面的雨雪停了；留下的印象：这个标题让人停了一下；"
        "链接：https://www.douyin.com/note/7525700842172534025"
    )

    text = harness._external_share_fallback_reference(source)

    assert "外面的雨雪停了" in text
    assert text.startswith("刚在抖音刷到")
    assert "，这个标题让我停了一下。https://" in text
    assert "《Bot》" not in text
    assert "《》" not in text  # fallback 不再使用书名号
