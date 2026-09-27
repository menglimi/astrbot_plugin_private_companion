# -*- coding: utf-8 -*-
from .proactive_core_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _ANONYMOUS_AREA_DWELL_THRESHOLDS_SECONDS,
    _ANONYMOUS_AREA_PENDING_TTL_SECONDS,
    _ANONYMOUS_AREA_STABLE_GAP_SECONDS,
    _ANONYMOUS_AREA_VISIT_GAP_SECONDS,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _MOBILE_LOCATION_HUMANIZATION_BUDGET_SECONDS,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _proactive_setting_value,
    logger,
)
from .proactive_core_shared import logger
from .proactive_core_part08 import ProactivePart08Mixin
from .proactive_core_part07 import ProactivePart07Mixin
from .proactive_core_part06 import ProactivePart06Mixin
from .proactive_core_part05 import ProactivePart05Mixin
from .proactive_core_part04 import ProactivePart04Mixin
from .proactive_core_part03 import ProactivePart03Mixin
from .proactive_core_part02 import ProactivePart02Mixin
from .proactive_core_part01 import ProactivePart01Mixin
from .proactive_core_shared import Any
from .proactive_core_shared import UserRestGateMixin
class ProactiveMixin(UserRestGateMixin, ProactivePart01Mixin, ProactivePart02Mixin, ProactivePart03Mixin, ProactivePart04Mixin, ProactivePart05Mixin, ProactivePart06Mixin, ProactivePart07Mixin, ProactivePart08Mixin):
    """主动消息调度"""

    _PROACTIVE_DAILY_LIMIT_UNLIMITED = 999_999
    _PROACTIVE_DAILY_QUOTA_MAX = 25
    _PROACTIVE_USER_DAILY_QUOTA_MAX = 30

    _PROACTIVE_QUOTA_TIER_POLICIES: dict[int, dict[str, Any]] = {
        0: {
            "label": "已关闭",
            "min_quota": 0,
            "max_quota": 0,
            "target_ratio": 0.0,
            "interval_cap_minutes": 0,
            "idle_cap_minutes": 0,
            "delay_range_hours": (0.0, 0.0),
            "unanswered_interval_weight": 1.0,
            "moment_probability_multiplier": 0.0,
            "candidate_score_bias": 0.0,
        },
        1: {
            "label": "克制",
            "min_quota": 1,
            "max_quota": 3,
            "target_ratio": 0.78,
            "interval_cap_minutes": 240,
            "idle_cap_minutes": 120,
            "delay_range_hours": (2.5, 8.0),
            "unanswered_interval_weight": 1.0,
            "moment_probability_multiplier": 0.82,
            "candidate_score_bias": -0.04,
        },
        2: {
            "label": "轻陪伴",
            "min_quota": 4,
            "max_quota": 7,
            "target_ratio": 0.86,
            "interval_cap_minutes": 150,
            "idle_cap_minutes": 75,
            "delay_range_hours": (1.25, 4.0),
            "unanswered_interval_weight": 0.72,
            "moment_probability_multiplier": 1.0,
            "candidate_score_bias": 0.0,
        },
        3: {
            "label": "稳定陪伴",
            "min_quota": 8,
            "max_quota": 12,
            "target_ratio": 0.92,
            "interval_cap_minutes": 90,
            "idle_cap_minutes": 45,
            "delay_range_hours": (0.65, 2.25),
            "unanswered_interval_weight": 0.42,
            "moment_probability_multiplier": 1.16,
            "candidate_score_bias": 0.04,
        },
        4: {
            "label": "亲密陪伴",
            "min_quota": 13,
            "max_quota": 18,
            "target_ratio": 0.97,
            "interval_cap_minutes": 55,
            "idle_cap_minutes": 25,
            "delay_range_hours": (0.38, 1.55),
            "unanswered_interval_weight": 0.18,
            "moment_probability_multiplier": 1.34,
            "candidate_score_bias": 0.08,
        },
        5: {
            "label": "持续在线",
            "min_quota": 19,
            "max_quota": None,
            "target_ratio": 1.0,
            "interval_cap_minutes": 35,
            "idle_cap_minutes": 10,
            "delay_range_hours": (0.22, 1.05),
            "unanswered_interval_weight": 0.0,
            "moment_probability_multiplier": 1.52,
            "candidate_score_bias": 0.12,
        },
    }

    _PROACTIVE_KIND_POLICIES: dict[str, dict[str, Any]] = {
        "transactional": {
            "label": "明确事务",
            "interval_multiplier": 0.2,
            "unanswered_score_penalty": 0.0,
            "score_bias": 0.16,
            "response_expectation": "none",
        },
        "continuation": {
            "label": "对话延续",
            "interval_multiplier": 0.65,
            "unanswered_score_penalty": 0.04,
            "score_bias": 0.08,
            "response_expectation": "optional",
        },
        "ritual": {
            "label": "日常仪式",
            "interval_multiplier": 0.82,
            "unanswered_score_penalty": 0.03,
            "score_bias": 0.04,
            "response_expectation": "optional",
        },
        "relational": {
            "label": "关系关怀",
            "interval_multiplier": 1.0,
            "unanswered_score_penalty": 0.08,
            "score_bias": 0.0,
            "response_expectation": "optional",
        },
        "self_life": {
            "label": "生活自述",
            "interval_multiplier": 0.86,
            "unanswered_score_penalty": 0.015,
            "score_bias": 0.02,
            "response_expectation": "none",
        },
        "content_share": {
            "label": "内容分享",
            "interval_multiplier": 0.9,
            "unanswered_score_penalty": 0.015,
            "score_bias": 0.03,
            "response_expectation": "none",
        },
        "safety_event": {
            "label": "安全与环境事件",
            "interval_multiplier": 0.12,
            "unanswered_score_penalty": 0.0,
            "score_bias": 0.2,
            "response_expectation": "none",
        },
    }

    _PROACTIVE_INTENSITY_PRESETS: dict[str, dict[str, Any]] = {
        "off": {
            "label": "关闭预设",
            "description": "沿用手动配置，不覆盖任何主动频率参数。",
            "effects": {},
        },
        "balanced": {
            "label": "标准偏主动",
            "description": "轻度提高主动触达，适合想比手动默认更有存在感但仍保持低打扰的场景。",
            "effects": {
                "max_daily_messages": 9,
                "idle_minutes": 40,
                "min_interval_minutes": 75,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.65,
                "friend_unanswered_max_cooldown_hours": 30,
                "friend_idle_floor_minutes": 60,
                "friend_min_interval_floor_minutes": 120,
                "delay_factor": 0.72,
                "proactive_persona_judge_send_threshold": 54,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 50,
                "group_high_intensity_cooldown_seconds": 105,
                "group_wakeup_interest_probability": 0.24,
                "group_wakeup_question_threshold": 60,
                "group_wakeup_cold_group_threshold": 62,
                "group_wakeup_topic_interest_max_boost": 0.55,
                "group_interject_min_interval_minutes": 90,
                "group_interject_max_daily": 4,
            },
        },
        "high_private": {
            "label": "私聊高频",
            "description": "显著提高主要用户私聊主动频率，适合希望 Bot 更常来找的用户。",
            "effects": {
                "max_daily_messages": 15,
                "idle_minutes": 14,
                "min_interval_minutes": 24,
                "unanswered_slowdown_start": 4,
                "unanswered_max_interval_multiplier": 1.25,
                "friend_unanswered_max_cooldown_hours": 14,
                "friend_idle_floor_minutes": 30,
                "friend_min_interval_floor_minutes": 60,
                "delay_factor": 0.42,
                "proactive_persona_judge_send_threshold": 45,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 45,
                "group_high_intensity_cooldown_seconds": 90,
                "group_wakeup_interest_probability": 0.22,
                "group_wakeup_question_threshold": 60,
                "group_wakeup_cold_group_threshold": 62,
                "group_wakeup_topic_interest_max_boost": 0.5,
                "group_interject_min_interval_minutes": 90,
                "group_interject_max_daily": 4,
            },
        },
        "high_group": {
            "label": "群聊活跃",
            "description": "明显提高群聊唤醒、兴趣词接话和群主动插话，私聊只轻度增强。",
            "effects": {
                "max_daily_messages": 8,
                "idle_minutes": 50,
                "min_interval_minutes": 95,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.8,
                "friend_unanswered_max_cooldown_hours": 36,
                "friend_idle_floor_minutes": 75,
                "friend_min_interval_floor_minutes": 150,
                "delay_factor": 0.75,
                "proactive_persona_judge_send_threshold": 56,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 20,
                "group_high_intensity_cooldown_seconds": 45,
                "group_wakeup_interest_probability": 0.45,
                "group_wakeup_question_threshold": 52,
                "group_wakeup_cold_group_threshold": 54,
                "group_wakeup_topic_interest_max_boost": 0.95,
                "group_interject_min_interval_minutes": 24,
                "group_interject_max_daily": 12,
            },
        },
        "live": {
            "label": "在线陪伴",
            "description": "最高在线陪伴档，每日主动上限 25 条，也不再替用户节省主动成本；仍会尊重免打扰、休息、拒绝、隐私和硬限额。",
            "effects": {
                "max_daily_messages": _PROACTIVE_DAILY_QUOTA_MAX,
                "idle_minutes": 0,
                "min_interval_minutes": 5,
                "unanswered_slowdown_start": 8,
                "unanswered_max_interval_multiplier": 1.0,
                "friend_unanswered_max_cooldown_hours": 8,
                "friend_idle_floor_minutes": 5,
                "friend_min_interval_floor_minutes": 15,
                "delay_factor": 0.08,
                "ignore_token_soft_limit": True,
                "ignore_soft_daily_target": True,
                "proactive_persona_judge_send_threshold": 32,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 3,
                "group_high_intensity_cooldown_seconds": 30,
                "group_wakeup_interest_probability": 0.78,
                "group_wakeup_question_threshold": 40,
                "group_wakeup_cold_group_threshold": 42,
                "group_wakeup_topic_interest_max_boost": 1.5,
                "group_interject_min_interval_minutes": 6,
                "group_interject_max_daily": _PROACTIVE_DAILY_LIMIT_UNLIMITED,
                "ignore_group_interject_daily_limit": True,
            },
        },
    }


# Re-export random for tests that patch astrbot_plugin_private_companion.proactive.random
from .proactive_core_part05 import random  # re-export for tests
