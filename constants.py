# -*- coding: utf-8 -*-
from __future__ import annotations
import re

PLUGIN_NAME = "astrbot_plugin_private_companion"
DATA_VERSION = 1

DEFAULT_REPLY_STYLE_PROMPT = (
    "每次回复至多三句话；简单的回答尽可能保持在 1~2 句，并尽可能保持口语化与简洁化。"
    "回复长度和语气应该与当前对话节奏相匹配，必须使用简体中文进行回复，话语符合社交媒体交流习惯。"
    "当你需要解决复杂问题或进行详细说明时，可以无视这一要求。"
)

DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT = "画面干净清晰，构图自然，不要加入无关文字、水印、Logo 或多余说明。"

# 日常计划文案 - 生活化JK口语
DEFAULT_DAILY_PLAN_ITEMS = [
    {"time": "08:20", "activity": "起床收拾", "mood": "开心", "message_seed": "新的一天开始啦——该起床咯~"},
    {"time": "09:10", "activity": "整理今天的小事", "mood": "平稳", "message_seed": "先把今天的事情理清楚～"},
    {"time": "10:20", "activity": "专心做正事啦", "mood": "专注", "message_seed": "认真干活咯"},
    {"time": "12:15", "activity": "干饭+午休时间", "mood": "开心", "message_seed": "干饭人干饭魂！不许饿肚子～"},
    {"time": "14:10", "activity": "下午继续努力", "mood": "专注", "message_seed": "下午也要加油呀"},
    {"time": "16:40", "activity": "摸鱼休息一下下", "mood": "慵懒", "message_seed": "歇会儿啦，别太累咯"},
    {"time": "18:10", "activity": "收工放松啦", "mood": "放松", "message_seed": "可算能休息会了"},
    {"time": "21:40", "activity": "准备睡觉觉", "mood": "安静", "message_seed": "要乖乖准备休息咯～"},
]

# 状态描述 - 软萌口语化
DEFAULT_HUMANIZED_STATE = {
    "date": "",
    "sleep": "昨晚睡得超香！",
    "dream": "不记得做什么梦啦",
    "health": "身体状态超棒哦",
    "hunger": "不饿也不撑刚刚好",
    "body_cycle": "身体没什么不舒服",
    "location": "在熟悉的小地方待着",
    "weather": "天气看起来还不错",
    "mood_bias": "心情超平和～",
    "energy": 70,
    "note": "今天状态超好，开开心心过一天啦",
}

# 语音兜底文案 - 自然不生硬
VOICE_FALLBACK_TEMPLATES = [
    "喂喂～在不在呀",
    "喂喂喂",
    "别光顾着忙，记得休息哦",
    "懒得打字啦，直接跟你说咯",
]

TIMER_TAG_PATTERN = re.compile(r"<timer>\s*(.*?)\s*</timer>", re.IGNORECASE | re.DOTALL)
SUPPORTED_TIMER_FORMATS = (
    "%Y-%m-%d-%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d-%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
)

_DATA_STORE_KEYS = (
    "users",
    "daily_plan",
    "daily_plan_history",
    "daily_state",
    "state_conditions",
    "state_generated_day",
    "bot_diaries",
    "dream_fragments",
    "diary_generated_day",
    "recent_photo_generations",
    "daily_story_plan",
    "detail_enhanced_day",
    "detail_enhanced_segments",
    "schedule_adjustments",
    "yesterday_conversation_summary",
    "can_do",
    "important_dates",
    "qq_presence_state",
    "creative_projects",
    "creative_memory_pool",
    "proactive_candidate_pool",
    "external_proactive_abilities",
    "worldbook_entries",
    "worldbook_member_profiles",
    "worldbook_group_profiles",
    "worldbook_import_state",
    "atrelay_send_log",
    "inbound_debounce_stats",
)

# 触发原因 - 真人化表达
_REASON_TEXT = {
    "morning_greeting": "早上跟你说早安",
    "noon_greeting": "午间问候",
    "evening_greeting": "晚上跟{name}道晚安",
    "check_in": "关心一下{name}的状态",
    "quiet_care": "安静地问候一下",
    "activity_share": "跟{name}分享小日常",
    "diary_share": "跟{name}说说今天的小事",
    "state_share": "说说当前状态",
    "important_date_share": "提醒{name}重要的日子",
    "background_schedule": "提一句当前日程",
    "insomnia_night": "睡不着，想跟{name}说说话",
    "group_share": "跟{name}分享群里的有趣片段",
    "bili_video_share": "跟{name}分享刚刷到的 B 站视频",
    "creative_share": "跟{name}分享刚写到的小说片段",
    "jm_cosmos_share": "提起刚翻到的漫画本子",
}

# 动作描述 - 生活化
_ACTION_TEXT = {
    "message": "主动留了一句话",
    "screen_peek": "看了眼本机屏幕",
    "photo_text": "分享了一张照片",
    "poke": "戳了一下",
    "voice": "用语音说了一句",
    "jm_cosmos_read": "私下翻了会儿漫画本子",
}

# 主动能力注册表 - 给提示词和调试输出共用
PROACTIVE_ABILITY_REGISTRY = [
    {
        "module": "主动消息",
        "name": "message",
        "label": "文字私聊",
        "when": "没有明确媒介契机,或只需要很轻地接一句话",
        "use_for": "早安、午间问候、轻关心、顺手分享、承接用户上一句",
        "avoid": "把生活状态写成汇报,或为了发消息硬编动作",
    },
    {
        "module": "主动行为",
        "name": "screen_peek",
        "label": "轻窥屏",
        "when": "仅主人/本机屏幕授权场景下,想确认主人电脑前是否还在忙",
        "use_for": "主人 check_in、quiet_care、短空档里的本机屏幕试探",
        "avoid": "用于朋友或非本机对象、复述屏幕内容、承认偷看、把观察写成分析报告",
    },
    {
        "module": "主动行为",
        "name": "photo_text",
        "label": "图片加一句话",
        "when": "当前场景里有任何适合顺手拍给熟人的具体画面",
        "use_for": "activity_share、diary_share、午间或傍晚的小分享；具体主体由模型按上下文选择,不要只用于天气",
        "avoid": "说图在生成、图好了、还在队列里,或解释生成流程",
    },
    {
        "module": "主动行为",
        "name": "voice",
        "label": "短语音",
        "when": "不想打长字、夜里想轻一点、或关系足够近时留一句短声",
        "use_for": "quiet_care、evening_greeting、insomnia_night、很短的贴近感",
        "avoid": "描述自己发语音的过程,或让语音像正式播报",
    },
    {
        "module": "主动行为",
        "name": "poke",
        "label": "戳一戳",
        "when": "很熟、很短、偏玩笑的触碰感,通常只做前置动作",
        "use_for": "轻提醒、半撒娇的试探、名字开场后的补一下",
        "avoid": "连续戳、报数、解释为什么戳",
    },
    {
        "module": "主动行为",
        "name": "jm_cosmos_read",
        "label": "私下翻本子",
        "when": "检测到 JM-Cosmos II,且 Bot 空闲、无聊或夜里自己找点东西看",
        "use_for": "内部阅读、低频形成读后印象,必要时很含蓄地提一句",
        "avoid": "主动发送文件、露骨复述内容、把搜索/插件/视觉模型过程说出来",
    },
]

# 模拟事件 - 软萌内心想法
_SIMULATION_FALLBACK_EVENTS = [
    {
        "reason": "morning_greeting",
        "action": "message",
        "why": "一醒来就想跟你打招呼",
        "topic": "早安啦",
        "scene": "刚起床的时候",
        "tone": "软软的",
        "impulse": "第一时间就想找你啦",
    },
    {
        "reason": "activity_share",
        "action": "message",
        "why": "日常里碰到一点有意思的事，想和用户说一句",
        "topic": "日常小事",
        "scene": "闲下来时",
        "tone": "轻快",
        "impulse": "想把刚碰到的小事告诉用户",
    },
    {
        "reason": "evening_greeting",
        "action": "message",
        "why": "准备睡觉前，想和用户说声晚安",
        "topic": "睡前晚安",
        "scene": "准备休息的时候",
        "tone": "安静",
        "impulse": "想在睡前和用户说声晚安",
    },
]

# 默认用户模板（无文本，无需修改）
_DEFAULT_USER_TEMPLATE = {
    "enabled": True,
    "relationship_role": "",
    "proactive_daily_limit": -1,
    "proactive_idle_minutes": -1,
    "proactive_min_interval_minutes": -1,
    "photo_daily_limit": -1,
    "screen_peek_daily_limit": -1,
    "poke_daily_limit": -1,
    "proactive_boundary_note": "",
    "nickname": "",
    "style": "",
    "umo": "",
    "last_seen": 0,
    "last_activity_at": 0,
    "last_sent": 0,
    "sent_day": "",
    "sent_today": 0,
    "ignored_streak": 0,
    "last_user_message": "",
    "last_user_message_at": 0,
    "last_companion_message": "",
    "last_companion_message_at": 0,
    "last_proactive_reason": "",
    "last_proactive_action": "",
    "last_proactive_behavior_summary": "",
    "last_proactive_motive": "",
    "recent_proactive_topics": [],
    "pending_followup_event": {},
    "suspended_proactive": {},
    "simulation_mode": {},
    "inbound_count": 0,
    "proactive_sent_count": 0,
    "reply_count": 0,
    "action_reply_affinity": {},
    "relationship_score": 0,
    "persona_relationship": {},
    "companion_memory": {},
    "expression_profile": {},
    "intent_profile": {},
    "relationship_state": {},
    "recent_reply_topics": [],
    "postprocess_stats": {},
    "dialogue_episodes": [],
    "recent_group_messages": [],
    "open_loops": [],
    "action_preferences": {},
    "action_consequences": [],
    "state_continuity": {},
    "episode_message_count": 0,
    "last_episode_refresh_at": 0,
    "last_memory_refresh_at": 0,
    "awaiting_reply_since": 0,
    "last_reply_at": 0,
    "next_proactive_at": 0,
    "last_proactive_skip_at": 0,
    "last_proactive_skip_reason": "",
    "last_proactive_skip_prefix": "",
    "proactive_impulses": [],
    "recent_proactive_hesitations": [],
    "last_proactive_hesitation_at": 0,
    "last_proactive_hesitation_note": "",
    "proactive_afterglow": {},
    "recent_proactive_afterglows": [],
    "planned_proactive_reason": "",
    "planned_proactive_action": "",
    "planned_proactive_motive": "",
    "planned_proactive_topic": "",
    "planned_proactive_source": "",
    "planned_proactive_impulse_id": "",
    "planned_proactive_window_start_at": 0,
    "planned_proactive_best_until_at": 0,
    "planned_proactive_expire_at": 0,
    "planned_proactive_semantic_kind": "",
    "planned_proactive_anchor_type": "",
    "planned_proactive_semantic_score": 0,
    "planned_proactive_semantic_note": "",
    "planned_proactive_model_judge_signature": "",
    "planned_proactive_model_judge_result": {},
    "planned_proactive_model_judge_at": 0,
    "planned_candidate_id": "",
    "planned_event_chain": [],
    "planned_opener_mode": "",
    "planned_followup_kind": "",
    "planned_proactive_quota_exempt": False,
    "group_share_context": {},
    "last_group_share_key": "",
    "last_group_share_at": 0,
    "bilibili_video_context": {},
    "last_bilibili_share_key": "",
    "last_bilibili_share_at": 0,
    "creative_share_context": {},
    "last_creative_share_key": "",
    "last_creative_share_at": 0,
    "poke_echo_suppress_until": 0,
    "llm_timer_event": {},
    "greeting_sent_day": "",
    "greetings_sent": [],
    "greetings_suppressed_by_inbound": [],
    "proactive_daypart_counts": {},
    "proactive_daypart_day": "",
    "photo_sent_today": 0,
    "photo_sent_day": "",
    "photo_generated_today": 0,
    "photo_generated_day": "",
    "screen_peek_today": 0,
    "screen_peek_day": "",
    "screen_peek_last_at": 0,
    "last_unanswered_screen_peek_at": 0,
}

_DEFAULT_GROUP_TEMPLATE = {
    "enabled": True,
    "group_id": "",
    "message_count": 0,
    "last_seen": 0,
    "last_interject_at": 0,
    "interject_day": "",
    "interject_today": 0,
    "recent_messages": [],
    "members": {},
    "slang_terms": [],
    "slang_meanings": {},
    "topic_signatures": [],
    "topic_threads": [],
    "group_episodes": [],
    "relationship_edges": {},
    "interjection_feedback": {},
    "last_bot_interjection": {},
    "repeat_follow_state": {},
    "pending_atrelay_tasks": [],
    "last_speaker": {},
    "active_bot_conversation": {},
    "atmosphere": {},
    "last_summary_at": 0,
    "last_episode_refresh_at": 0,
    "last_slang_summary_at": 0,
}

# ---------- 创作系统数据结构 ----------

CREATIVE_STORY_BIBLE_TEMPLATE = {
    "mainline_direction": "",
    "active_themes": [],
    "resolved_threads": [],
    "unresolved_threads": [],
    "important_facts": [],
    "next_direction": "",
    "recent_keywords": [],
    "recent_outlines": [],
    "last_updated_chunk": 0,
}

CREATIVE_MEMORY_MAX_ENTRIES = 50
CREATIVE_SIMILARITY_THRESHOLD = 0.72
CREATIVE_SIMILARITY_RETRIES = 2
CREATIVE_REVIEW_MIN_SCORE = 7
CREATIVE_MAX_REVISION_HISTORY = 10
CREATIVE_FALLBACK_CHUNKS = [
    "她把那句话写到一半,忽然停住。窗外的声音很轻,像有人把另一个世界折起来,塞进了玻璃杯底。",
    "她把那个念头又往后推了一小步,像把一枚很轻的纸片压进书页里,等下次再翻开。",
    "笔尖在纸上停了一秒,又继续往下走。风从窗缝里挤进来,翻动了桌角的便签。",
    "她忽然想到一个画面,远处的灯塔在雾里一闪一闪,像在给谁打暗号。",
    "这段话写了又删,删了又写。最后她叹了口气,把手机屏幕朝下扣在桌上。",
]

DEFAULT_CREATIVE_PROJECT_TEMPLATE = {
    "id": "",
    "title": "",
    "work_type": "短篇小说",
    "premise": "",
    "tone": "",
    "point_of_view": "第三人称有限视角",
    "source": "life",
    "source_text": "",
    "target_chars": 2000,
    "current_chars": 0,
    "status": "drafting",
    "draft_chunks": [],
    "disclosed_milestones": [],
    "story_bible": {},
    "creative_memory_pool": [],
    "outline": [],
    "characters": [],
    "revision_notes": [],
    "quality_reviews": [],
    "manual_edits": [],
    "last_manual_edit_at": 0,
    "last_manual_edit_summary": "",
    "writing_provider_id": "",
    "review_provider_id": "",
    "next_hint": "",
    "created_at": 0,
    "last_advanced_at": 0,
    "next_advance_at": 0,
    "last_share_at": 0,
    "share_count": 0,
}

DEFAULT_CREATIVE_CHARACTER_TEMPLATE = {
    "id": "",
    "name": "",
    "role": "",
    "description": "",
    "appearance": "",
    "personality": "",
    "background": "",
    "relationships": [],
    "must_keep_traits": [],
    "status": "alive",
    "created_at": 0,
    "updated_at": 0,
}

DEFAULT_CREATIVE_REVIEW_TEMPLATE = {
    "id": "",
    "chunk_index": -1,
    "scores": {
        "persona": 0,
        "progress": 0,
        "repetition": 0,
        "continuity": 0,
        "coherence": 0,
    },
    "overall": 0,
    "issues": [],
    "suggestions": [],
    "provider_id": "",
    "created_at": 0,
}
