from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _DuplicateGuardHarness(DailyStateMixin):
    pass


def test_current_proactive_candidate_is_not_treated_as_sent_history():
    harness = _DuplicateGuardHarness()
    now = 1_000_100.0
    text = "揉了下眼睛，忽然想起刚刷到的有趣视频。"
    user = {
        "last_companion_message": text,
        "last_companion_message_at": now - 5,
        "proactive_sending": True,
        "proactive_sending_started_at": now - 30,
        "recent_proactive_topics": [],
    }

    reason = harness._recent_proactive_text_duplicate_reason(user, text=text, now=now)

    assert reason == ""


def test_confirmed_message_before_current_proactive_still_blocks_duplicate():
    harness = _DuplicateGuardHarness()
    now = 1_000_100.0
    text = "揉了下眼睛，忽然想起刚刷到的有趣视频。"
    user = {
        "last_companion_message": text,
        "last_companion_message_at": now - 120,
        "proactive_sending": True,
        "proactive_sending_started_at": now - 30,
        "recent_proactive_topics": [],
    }

    reason = harness._recent_proactive_text_duplicate_reason(user, text=text, now=now)

    assert "聊天里已经说过相似内容" in reason


def test_inbound_reply_time_does_not_refresh_old_companion_message():
    harness = _DuplicateGuardHarness()
    now = 1_000_100.0
    text = "揉了下眼睛，忽然想起刚刷到的有趣视频。"
    user = {
        "last_companion_message": text,
        "last_companion_message_at": 0,
        "last_reply_at": now - 5,
        "proactive_sending": True,
        "proactive_sending_started_at": now - 30,
        "recent_proactive_topics": [],
    }

    reason = harness._recent_proactive_text_duplicate_reason(user, text=text, now=now)

    assert reason == ""


def test_ordinary_weather_variants_share_one_long_lived_topic():
    harness = _DuplicateGuardHarness()
    now = 1_000_100.0
    user = {
        "recent_proactive_topics": [
            {
                "ts": now - 12 * 3600,
                "signature": "ordinary_weather_topic",
                "text": "外面开始下雨了。",
            }
        ]
    }

    signature = harness._proactive_topic_signature("今天气温降下来了，你那边冷不冷？")

    assert signature == "ordinary_weather_topic"
    assert harness._recent_proactive_topic_repeated(user, signature, now=now)


def test_non_weather_outdoor_topic_is_not_collapsed_into_weather():
    harness = _DuplicateGuardHarness()

    signature = harness._proactive_topic_signature("我在外面吃饭，刚碰到一家小店。")

    assert signature != "ordinary_weather_topic"


def test_legacy_weather_topic_is_migrated_during_cleanup():
    harness = _DuplicateGuardHarness()
    now = 1_000_100.0
    user = {
        "recent_proactive_topics": [
            {
                "ts": now - 8 * 3600,
                "signature": "morning_weather_check",
                "text": "早呀，外面天阴阴的，好想赖床。",
            }
        ]
    }

    recent = harness._cleanup_recent_proactive_topics(user, now=now)

    assert len(recent) == 1
    assert recent[0]["signature"] == "ordinary_weather_topic"
