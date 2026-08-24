# -*- coding: utf-8 -*-
"""
CreativeMixin — 从 main.py 重新拆分出的创作系统
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .constants import (
    CREATIVE_MEMORY_MAX_ENTRIES,
    CREATIVE_MAX_REVISION_HISTORY,
    CREATIVE_REVIEW_MIN_SCORE,
    CREATIVE_SIMILARITY_RETRIES,
    CREATIVE_SIMILARITY_THRESHOLD,
    CREATIVE_STORY_BIBLE_TEMPLATE,
    CREATIVE_FALLBACK_CHUNKS,
    CREATIVE_LEGACY_FALLBACK_CHUNKS,
)
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line, _text_similarity
from .persona_config import runtime_persona_setting


def _persona_provider_id(owner: Any, canonical_key: str, legacy_attr: str, quick_role: str) -> str:
    """Resolve canonical persona provider settings while preserving test harnesses."""
    fallback = str(getattr(owner, legacy_attr, "") or "").strip()
    if not callable(getattr(owner, "persona_setting", None)):
        return fallback
    mode = str(getattr(owner, "provider_config_mode", "quick") or "quick").strip().lower()
    if mode != "quick":
        return str(runtime_persona_setting(owner, canonical_key, fallback) or "").strip()
    complex_id = str(runtime_persona_setting(owner, "COMPLEX_REASONING_PROVIDER_ID", "") or "").strip()
    if quick_role == "complex":
        return complex_id or fallback
    if quick_role == "creative":
        creative_id = str(runtime_persona_setting(owner, "CREATIVE_MODEL_PROVIDER_ID", "") or "").strip()
        return creative_id or complex_id or fallback
    fast_id = str(runtime_persona_setting(owner, "FAST_RESPONSE_PROVIDER_ID", "") or "").strip()
    return fast_id or complex_id or fallback

DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
        DEFAULT_AI_DAILY_NEWS_SOURCE,
    ]
)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)



_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}

class CreativeMixin:
    """创作系统"""

    def _creative_projects(self) -> list[dict[str, Any]]:
        projects = self.data.setdefault("creative_projects", [])
        if not isinstance(projects, list):
            projects = []
            self.data["creative_projects"] = projects
        valid_projects = [item for item in projects if isinstance(item, dict)]
        for project in valid_projects:
            project.setdefault("work_type", "短篇小说")
            point_of_view = _single_line(project.get("point_of_view"), 40)
            if not point_of_view:
                project["point_of_view"] = "第三人称有限视角"
                project.setdefault("point_of_view_policy_version", 2)
                continue
            if (
                "第一人称" in point_of_view
                and not project.get("point_of_view_policy_version")
                and "书信" not in point_of_view
                and "日记" not in point_of_view
                and "手记" not in point_of_view
            ):
                project["point_of_view"] = "第三人称有限视角"
                project["point_of_view_note"] = "legacy_first_person_rebalanced"
                project["point_of_view_policy_version"] = 2
        return valid_projects

    def _creative_chars_per_session(self) -> int:
        style = str(runtime_persona_setting(self, "default_style", "温柔") or "")
        persona = "{} {} {}".format(
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "default_style", "温柔"),
            runtime_persona_setting(self, "bot_name", "小星"),
        )
        budget = runtime_persona_setting(self, "creative_chars_per_session", 220)
        if any(token in persona for token in ("慢热", "寡言", "内敛", "病弱", "疲惫", "懒", "迟钝")):
            budget = int(budget * 0.72)
        elif any(token in persona for token in ("活泼", "话多", "元气", "急性子")) or style == "活泼":
            budget = int(budget * 1.18)
        elif style == "校园风":
            budget = int(budget * 0.88)
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if energy < 40:
            budget = int(budget * 0.72)
        elif energy > 82:
            budget = int(budget * 1.12)
        return max(60, min(1200, budget))

    def _creative_advance_gap_minutes(
        self,
        project: dict[str, Any] | None = None,
        now: float | None = None,
        *,
        initial: bool = False,
    ) -> int:
        """拟人化写作节奏：按时段与人格决定下次推进间隔（分钟）。

        晚间是随手写两笔的黄金档，早晨在忙别的事；慢热型人格提笔更慢，
        偶尔"写得顺手"会在很短时间内再写一段，但不连续爆发。
        """
        now = _now_ts() if now is None else float(now)
        low, high = (35, 130) if initial else (95, 320)
        now_dt = datetime.fromtimestamp(now)
        if 20 <= now_dt.hour < 23 or (now_dt.hour == 23 and now_dt.minute <= 30):
            low, high = max(25, int(low * 0.7)), int(high * 0.62)
        elif 7 <= now_dt.hour < 10:
            low, high = int(low * 1.8), int(high * 1.3)
        persona = " ".join(
            str(runtime_persona_setting(self, name, "") or "")
            for name in ("schedule_persona_prompt", "default_style", "bot_name")
        )
        if any(token in persona for token in ("慢热", "寡言", "内敛", "病弱", "疲惫", "懒", "迟钝")):
            low, high = int(low * 1.35), int(high * 1.35)
        elif any(token in persona for token in ("活泼", "话多", "元气", "急性子")):
            low, high = int(low * 0.8), int(high * 0.8)
        project_dict = project if isinstance(project, dict) else {}
        last_burst = _safe_float(project_dict.get("last_creative_burst_at"), 0, 0)
        if (
            not initial
            and (not last_burst or now - last_burst >= 3 * 3600)
            and random.random() < 0.15
        ):
            project_dict["last_creative_burst_at"] = now
            return random.randint(25, 60)
        return random.randint(low, high)

    def _creative_chunk_budget_for(self, project: dict[str, Any] | None, base_budget: int) -> int:
        """按作品类型收紧单段字数：短诗小步快写，长叙事维持原有节奏。"""
        work_type = self._creative_work_type(project)
        budget = max(60, int(base_budget))
        if any(token in work_type for token in ("诗", "歌词", "短句")):
            return min(budget, 150)
        if any(token in work_type for token in ("随笔", "散文", "札记", "观察", "设定", "图鉴", "怪谈")):
            return min(budget, 400)
        return budget

    def _bot_currently_idle_for_creative_writing(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 7:
            return False
        current_item = self._creative_current_agenda_item()
        if self._is_sleepy_plan_item(current_item):
            return False
        activity = _single_line((current_item or {}).get("activity"), 100)
        mood = _single_line((current_item or {}).get("mood"), 40)
        seed = _single_line((current_item or {}).get("message_seed"), 100)
        state = self.data.get("daily_state", {})
        state_mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 30)
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        text = f"{activity} {mood} {seed} {state_mood}"
        busy_tokens = (
            "上课", "学习", "复习", "考试", "作业", "工作", "开会", "通勤",
            "忙", "赶", "处理", "训练", "任务", "外出", "出门", "睡",
        )
        if any(token in text for token in busy_tokens):
            return False
        idle_tokens = (
            "创作", "写字", "写作", "灵感", "读书", "阅读", "休息", "摸鱼",
            "发呆", "无聊", "闲", "空", "散步", "听歌", "整理", "安静",
            "下午也要加油", "缓一缓", "歇", "偷懒",
        )
        if any(token in text for token in idle_tokens):
            return random.random() < 0.55
        return 38 <= energy <= 82 and random.random() < 0.18

    def _creative_has_pending_proactive_plan(self) -> bool:
        now = _now_ts()
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return False
        for user in users.values():
            if not isinstance(user, dict):
                continue
            if bool(user.get("proactive_sending")):
                return True
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if 0 < next_at - now <= 20 * 60:
                return True
        return False

    @staticmethod
    def _creative_fallback_signature(text: Any) -> str:
        normalized = re.sub(r"\s+", "", str(text or "")).strip()
        return normalized.translate(str.maketrans({"，": ",", "；": ";", "：": ":"}))

    @classmethod
    def _is_legacy_creative_fallback_chunk(cls, text: Any) -> bool:
        signature = cls._creative_fallback_signature(text)
        if not signature:
            return False
        return signature in {
            cls._creative_fallback_signature(item)
            for item in (*CREATIVE_FALLBACK_CHUNKS, *CREATIVE_LEGACY_FALLBACK_CHUNKS)
        }

    def _cleanup_legacy_creative_fallback_chunks(self) -> bool:
        projects = self.data.get("creative_projects") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(projects, list):
            return False
        removed_chunks = 0
        cleaned_projects = 0
        removed_memories = 0
        for project in projects:
            if not isinstance(project, dict):
                continue
            chunks = project.get("draft_chunks")
            if not isinstance(chunks, list):
                continue
            kept_chunks = [
                chunk
                for chunk in chunks
                if not (
                    isinstance(chunk, dict)
                    and self._is_legacy_creative_fallback_chunk(chunk.get("text"))
                )
            ]
            removed_here = len(chunks) - len(kept_chunks)
            if removed_here <= 0:
                continue
            project["draft_chunks"] = kept_chunks
            project["current_chars"] = sum(
                len(str(chunk.get("text") or "").strip())
                for chunk in kept_chunks
                if isinstance(chunk, dict)
            )
            project["legacy_fallback_chunks_removed"] = (
                _safe_int(project.get("legacy_fallback_chunks_removed"), 0, 0) + removed_here
            )
            pool = project.get("creative_memory_pool")
            if isinstance(pool, list):
                kept_pool = [
                    item
                    for item in pool
                    if not (
                        isinstance(item, dict)
                        and self._is_legacy_creative_fallback_chunk(item.get("content"))
                    )
                ]
                removed_memories += len(pool) - len(kept_pool)
                project["creative_memory_pool"] = kept_pool
            removed_chunks += removed_here
            cleaned_projects += 1
        if removed_chunks:
            logger.info(
                "[PrivateCompanion] 已清理资料柜旧版固定兜底片段: projects=%s chunks=%s memories=%s",
                cleaned_projects,
                removed_chunks,
                removed_memories,
            )
            return True
        return False

    def _creative_persona_style_context(self) -> str:
        default_persona = _single_line(self._get_default_persona_prompt(), 700)
        schedule_persona = _single_line(runtime_persona_setting(self, "schedule_persona_prompt", ""), 500)
        style = _single_line(runtime_persona_setting(self, "default_style", "温柔"), 80)
        bot_name = _single_line(runtime_persona_setting(self, "bot_name", "小星"), 40)
        creative_voice = ""
        voice_formatter = getattr(self, "_format_persona_voice_channel_prompt", None)
        if callable(voice_formatter):
            creative_voice = voice_formatter("creative")
        return "\n".join(
            part
            for part in (
                f"Bot 名称：{bot_name}" if bot_name else "",
                f"AstrBot 默认人格：{default_persona}" if default_persona else "",
                f"日程/生活人设补充：{schedule_persona}" if schedule_persona else "",
                f"默认对话风格：{style}" if style else "",
                creative_voice,
                "创作要求：从人格日常在意的事物里取细节——人格关注什么,笔下的名词就落在什么上；句子的节奏、比喻的习惯和收束方式,都从这个人格的说话方式里长出来,不要写成谁都能写的通用文本。",
                "性别与代词边界：只有人格资料明确指定性别或代词时才使用；未指定时不要默认女性或男性，可用角色名、Bot、角色或省略代词。温柔、细腻、理性、锋利等表达气质不绑定任何性别。",
                "身份边界：如果人格没有学生、职场、异世界、职业、年龄、身体特征等设定,不要凭空添加；如果人格明确不是人类,不要写成人类日常生理经验。",
                "文风边界：不要套用通用网文腔、营销文案腔或过度华丽散文腔；不要为了梦境感牺牲可读性。",
            )
            if part
        )

    def _creative_point_of_view(self, project: dict[str, Any] | None = None) -> str:
        if isinstance(project, dict):
            point_of_view = _single_line(project.get("point_of_view"), 40)
        else:
            point_of_view = ""
        return point_of_view or "第三人称有限视角"

    def _creative_work_type(self, project: dict[str, Any] | None = None) -> str:
        work_type = _single_line(project.get("work_type") if isinstance(project, dict) else "", 30)
        return work_type or "短篇小说"

    def _creative_work_output_rule(self, work_type: str, point_of_view: str) -> str:
        work_type = _single_line(work_type, 30) or "短篇小说"
        if any(token in work_type for token in ("诗", "短诗", "歌词", "歌")):
            return "只输出本次写下的诗句/歌词片段,可以换行,不要解释意象,不要写成小说叙事。"
        if any(token in work_type for token in ("随笔", "散文", "札记", "观察", "影评", "读后感")):
            return "只输出本次写下的随笔/札记正文,可以有作者自己的观察,但不要写成对用户的聊天回复或系统汇报。"
        if any(token in work_type for token in ("剧本", "短剧", "分镜", "脚本", "对白")):
            return "只输出本次写下的剧本/分镜/对白片段,允许出现角色名和简短舞台提示,不要写成完整成片方案。"
        if any(token in work_type for token in ("设定", "世界观", "角色", "怪谈", "图鉴")):
            return "只输出本次补上的设定正文,可以像设定集、图鉴或角色档案,但要保留作品感,不要写成插件配置。"
        return f"只输出本次写下的正文片段。叙事视角规则：{self._creative_point_of_view_rule(point_of_view)}"

    def _creative_point_of_view_rule(self, point_of_view: str) -> str:
        pov = _single_line(point_of_view, 40) or "第三人称有限视角"
        if "第一人称" in pov:
            return (
                "本项目允许第一人称叙述,但叙述者应是小说角色,不是 Bot 本人在写日记；"
                "除非设定明确,不要把作者身份直接塞进正文。"
            )
        if "书信" in pov or "日记" in pov or "手记" in pov:
            return (
                f"按“{pov}”写作,可以出现文本载体中的自称,但要保持它属于故事内部角色；"
                "不要写成 Bot 对用户的日常汇报。"
            )
        return (
            f"严格按“{pov}”写作。正文不要用“我”作为叙述者,角色台词里的“我”可以保留；"
            "不要写成日记、自述或作者独白。"
        )

    # ============================================================
    # Story Bible / Memory Pool / Outline / Characters
    # ============================================================

    def _get_or_create_story_bible(self, project: dict[str, Any]) -> dict[str, Any]:
        story_bible = project.get("story_bible")
        if not isinstance(story_bible, dict):
            story_bible = deepcopy(CREATIVE_STORY_BIBLE_TEMPLATE)
            story_bible["mainline_direction"] = _single_line(project.get("premise"), 120)
            story_bible["next_direction"] = _single_line(project.get("next_hint"), 120)
            project["story_bible"] = story_bible
        for key, default in CREATIVE_STORY_BIBLE_TEMPLATE.items():
            if key not in story_bible or not isinstance(story_bible.get(key), type(default)):
                story_bible[key] = deepcopy(default)
        return story_bible

    def _get_or_create_memory_pool(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        pool = project.get("creative_memory_pool")
        if not isinstance(pool, list):
            pool = []
            project["creative_memory_pool"] = pool
        valid = [item for item in pool if isinstance(item, dict)]
        if len(valid) != len(pool):
            pool[:] = valid
        return pool

    def _extract_creative_keywords(self, text: Any, *, limit: int = 10) -> list[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9_\-]{3,24}", str(text or ""))
        keywords: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            item = token.strip().lower()
            if item and item not in seen:
                seen.add(item)
                keywords.append(item)
                if len(keywords) >= limit:
                    break
        return keywords

    def _retrieve_relevant_memories(
        self, pool: list[dict[str, Any]], project_id: str,
        keywords_hint: list[str], limit: int = 8,
    ) -> list[dict[str, Any]]:
        hints = {w.strip().lower() for w in keywords_hint if w.strip()}
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in pool:
            if not isinstance(entry, dict) or str(entry.get("project_id") or "") != str(project_id):
                continue
            entry_kw = {w.strip().lower() for w in (entry.get("keywords") or []) if isinstance(w, str) and w.strip()}
            overlap = len(hints & entry_kw)
            importance = _safe_int(entry.get("importance"), 2, 1, 5)
            score = overlap * 2.0 + importance * 0.5
            if score > 0 or not hints:
                scored.append((score, entry))
        scored.sort(key=lambda x: (x[0], _safe_float(x[1].get("created_at"), 0.0)), reverse=True)
        return [e for _, e in scored[:limit]]

    def _add_memory_entry(
        self, pool: list[dict[str, Any]], project_id: str,
        entry_type: str, content: Any, keywords: list[str], importance: int = 2,
    ) -> None:
        text = _single_line(content, 200)
        if not text:
            return
        kw_src = " ".join(str(k) for k in keywords) if isinstance(keywords, list) else (keywords or text)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "type": _single_line(entry_type, 24) or "scene",
            "content": text,
            "keywords": self._extract_creative_keywords(kw_src, limit=10),
            "importance": _safe_int(importance, 2, 1, 5),
            "created_at": _now_ts(),
            "project_id": str(project_id or ""),
        }
        sig = _single_line(text, 120)
        for item in pool:
            if isinstance(item, dict) and str(item.get("project_id") or "") == str(project_id):
                if _text_similarity(item.get("content"), sig) >= 0.88:
                    item["importance"] = max(_safe_int(item.get("importance"), 2, 1, 5), entry["importance"])
                    return
        pool.append(entry)
        pool.sort(key=lambda x: (_safe_int(x.get("importance"), 2, 1, 5), _safe_float(x.get("created_at"), 0.0)), reverse=True)
        del pool[CREATIVE_MEMORY_MAX_ENTRIES:]

    def _check_chunk_similarity(self, new_text: str, recent_chunks: list[dict[str, Any]]) -> bool:
        for item in recent_chunks[-12:]:
            if not isinstance(item, dict):
                continue
            existing = _single_line(item.get("text"), 800)
            if existing and _text_similarity(new_text, existing) >= CREATIVE_SIMILARITY_THRESHOLD:
                return True
        return False

    def _get_project_characters(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        chars = project.get("characters")
        if not isinstance(chars, list):
            chars = []
            project["characters"] = chars
        return [c for c in chars if isinstance(c, dict)]

    def _normalize_outline_text(self, text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lines: list[str] = []
        for line in re.split(r"\r?\n+", raw):
            cleaned = re.sub(r"^[\-•*\d\.\)\s]+", "", line or "").strip()
            if cleaned:
                lines.append(f"- {_single_line(cleaned, 36)}")
            if len(lines) >= 5:
                break
        if not lines:
            compact = [_single_line(p, 36) for p in re.split(r"[；;，,]", raw) if _single_line(p, 36)]
            lines = [f"- {p}" for p in compact[:5]]
        return "\n".join(lines)

    def _creative_active_project_briefs(self, projects: list[dict[str, Any]], *, limit: int = 3) -> str:
        lines: list[str] = []
        for item in projects[-limit:]:
            if not isinstance(item, dict):
                continue
            t = _single_line(item.get("title"), 24)
            p = _single_line(item.get("premise"), 80)
            if t or p:
                lines.append(f"- {t or '未命名'} / {p or '无设定'}")
        return "\n".join(lines)

    def _creative_recent_chunk_digest(self, chunks: list[dict[str, Any]], *, limit: int = 5) -> str:
        lines: list[str] = []
        start = max(1, len(chunks) - limit + 1)
        for idx, item in enumerate(chunks[-limit:], start=start):
            if isinstance(item, dict):
                t = _single_line(item.get("text"), 120)
                if t:
                    lines.append(f"{idx}. {t}")
        return "\n".join(lines) or "暂无最近片段。"

    def _creative_manual_outline_context(self, project: dict[str, Any], *, limit: int = 12) -> str:
        outline = project.get("outline") if isinstance(project.get("outline"), list) else []
        lines: list[str] = []
        for idx, item in enumerate(outline[:limit], start=1):
            text = _single_line(item, 120)
            if text:
                lines.append(f"{idx}. {text}")
        return "\n".join(lines)

    def _creative_character_context(self, project: dict[str, Any], *, limit: int = 8) -> str:
        characters = self._get_project_characters(project)
        lines: list[str] = []
        for item in characters[:limit]:
            name = _single_line(item.get("name"), 32) or "未命名角色"
            role = _single_line(item.get("role"), 36)
            desc = _single_line(item.get("description") or item.get("personality") or item.get("background"), 120)
            appearance = _single_line(item.get("appearance"), 80)
            traits = item.get("must_keep_traits") if isinstance(item.get("must_keep_traits"), list) else []
            trait_text = "、".join(_single_line(t, 20) for t in traits if _single_line(t, 20))
            parts = [name]
            if role:
                parts.append(role)
            if desc:
                parts.append(desc)
            if appearance:
                parts.append(f"外貌：{appearance}")
            if trait_text:
                parts.append(f"必须保留：{trait_text}")
            lines.append("- " + "｜".join(parts))
        return "\n".join(lines)

    def _creative_manual_revision_context(self, project: dict[str, Any], *, limit: int = 5) -> str:
        edits = project.get("manual_edits") if isinstance(project.get("manual_edits"), list) else []
        lines: list[str] = []
        summary = _single_line(project.get("last_manual_edit_summary"), 120)
        if summary:
            lines.append(f"- 最近人工修订：{summary}")
        for item in edits[-limit:]:
            if not isinstance(item, dict):
                continue
            title = _single_line(item.get("title") or item.get("type"), 60)
            content = _single_line(item.get("content"), 160)
            chunk_index = _safe_int(item.get("chunk_index"), -1, -1)
            prefix = f"第{chunk_index + 1}段" if chunk_index >= 0 else "项目"
            if title or content:
                lines.append(f"- {prefix}｜{title or '人工修订'}：{content}")
        return "\n".join(lines[-limit:])

    def _inspiration_already_used(self, source_text: str, active_projects: list[dict[str, Any]]) -> bool:
        if not source_text:
            return False
        src_kw = set(self._extract_creative_keywords(source_text, limit=8))
        if not src_kw:
            return False
        for p in active_projects:
            if not isinstance(p, dict) or p.get("status") != "drafting":
                continue
            used_kw = set(self._extract_creative_keywords(p.get("source_text"), limit=8))
            sb = p.get("story_bible") if isinstance(p.get("story_bible"), dict) else {}
            used_kw |= {_single_line(t, 16).lower() for t in sb.get("recent_keywords", []) if _single_line(t, 16)}
            if used_kw:
                overlap = len(src_kw & used_kw) / max(1, len(src_kw | used_kw))
                if overlap > 0.5:
                    return True
        return False

    def _creative_inspiration_source(self) -> dict[str, str] | None:
        active_projects = [p for p in self._creative_projects() if p.get("status") == "drafting"]
        current_item = self._creative_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 90)
        seed = _single_line((current_item or {}).get("message_seed"), 90)
        dream = self.data.get("daily_dream")
        dream_text = ""
        if isinstance(dream, dict):
            dream_text = _single_line(dream.get("content") or dream.get("label"), 180)
        diary = self.data.get("bot_diaries", [])
        diary_text = ""
        if isinstance(diary, list) and diary:
            latest = diary[-1]
            if isinstance(latest, dict):
                diary_text = _single_line(latest.get("share_seed") or latest.get("summary"), 140)
        candidates = []
        if dream_text and random.random() < 0.46 and not self._inspiration_already_used(dream_text, active_projects):
            candidates.append({"source": "dream", "text": dream_text, "label": "梦境余温"})
        life_text = " / ".join(part for part in (activity, seed) if part)
        if activity and not self._inspiration_already_used(life_text, active_projects):
            candidates.append({"source": "life", "text": life_text, "label": "生活小事"})
        if diary_text and not self._inspiration_already_used(diary_text, active_projects):
            candidates.append({"source": "diary", "text": diary_text, "label": "日记碎片"})
        if not candidates:
            return None
        return random.choice(candidates)

    async def _generate_creative_project(self, source: dict[str, str]) -> dict[str, Any] | None:
        source_text = _single_line(source.get("text"), 220)
        source_label = _single_line(source.get("label"), 24) or "小灵感"
        persona_context = self._creative_persona_style_context()
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="creative_project",
                    query=(
                        f"私下创作立项：灵感={source_label} {source_text}；"
                        "用户创作偏好、长期项目、最近修订、避雷、喜欢的题材、上次写到哪里"
                    ),
                    top_k=5,
                    max_chars=900,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创作立项 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
 你是一个拟人化 Bot 的私人创作状态生成器。这个 Bot/角色会因为一个生活小事、日记碎片或梦境灵感,突然想开一个自己的创作项目。

【人格与身份】
{persona_context}

【我会牢牢记住你 创作连续性参考】
{memory_context or '暂无外部长期创作记忆。'}
使用方式：优先尊重用户长期偏好、已有项目、人工修订和避雷；不要说自己“查了记忆”。

【用户配置的创作方向】
{direction_prompt or '未指定，按人格、灵感和连续性自然决定。'}

要求：
1. 只设计“正在做的创作计划”,不要写正文。
2. 作品类型可以是短篇小说、诗/歌词、随笔/散文、短剧/对白、分镜脚本、角色设定、世界观片段、怪谈、图鉴条目或其他符合人格的文本作品；不要固定为小说。
 3. 风格必须贴合上面的人格、身份和默认说话气质；标题、设定和 tone 都要像这个人格自然会想到的。
4. 灵感来源：{source_label}｜{source_text}
5. 目标 300-5200 字。诗/歌词/短设定可以较短,小说/剧本/世界观可以较长；不能一次写完。
6. 题材可以日常、轻奇幻、悬疑、校园、都市、梦境感、观察、角色小传、世界碎片等,但不要色情、血腥或攻击性。
7. 不要为了题材方便凭空改变 Bot 身份,也不要写出和人格不相称的成熟度、职业经验或生活经验。
8. 作者人格只决定选题、审美、句子节奏和观察方式,不等于正文必须用第一人称。
9. 如果 work_type 不是叙事类,point_of_view 可写“无固定叙事视角”。
10. 开场要落在故事内一个具体时刻（如"初秋的傍晚""期末考前一周的深夜"）,写进 opening_story_time,不要写"某个平常的日子"这类模糊时间。
11. 输出 JSON。

格式：
{{
  "work_type": "作品类型,如短篇小说/短诗/随笔/短剧/分镜脚本/角色设定/世界观片段",
  "title": "临时标题,不要超过18字",
  "premise": "一句话核心设定",
  "tone": "行文气质,2到5个词",
  "point_of_view": "第三人称有限视角/第三人称全知视角/多视角/第一人称角色视角/书信体/无固定叙事视角之一",
  "target_chars": 目标字数数字,
  "opening_story_time": "故事开场的具体时刻,10到20字",
  "next_hint": "第一段准备写什么"
}}
""".strip()
        text = await self._llm_call(
            prompt,
            max_tokens=500,
            provider_id=self._task_provider(
                _persona_provider_id(self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="creative_project",
        )
        payload = self._extract_json_payload(text or "")
        if not isinstance(payload, dict):
            payload = {}
        title = _single_line(payload.get("title"), 24) or random.choice(["玻璃杯里的小雨", "迟到的梦", "窗边备用宇宙"])
        work_type = _single_line(payload.get("work_type"), 30) or "短篇小说"
        target_chars = _safe_int(payload.get("target_chars"), random.randint(900, 2800), 300, 5200)
        now = _now_ts()
        project_id = uuid.uuid4().hex[:12]
        story_bible = {
            "mainline_direction": _single_line(payload.get("premise"), 140) or f"围绕{source_label}延伸出一个逐步展开的小作品",
            "active_themes": self._extract_creative_keywords(source_text, limit=3),
            "resolved_threads": [],
            "unresolved_threads": [_single_line(payload.get("next_hint"), 40)] if _single_line(payload.get("next_hint"), 40) else [],
            "important_facts": [],
            "next_direction": _single_line(payload.get("next_hint"), 120) or "先写一个很小的开场画面",
            "story_time": _single_line(payload.get("opening_story_time"), 60),
            "recent_keywords": self._extract_creative_keywords(source_text, limit=6),
            "recent_outlines": [],
            "last_updated_chunk": 0,
        }
        project = {
            "id": project_id,
            "title": title,
            "work_type": work_type,
            "premise": _single_line(payload.get("premise"), 140) or f"从{source_label}里长出来的一个短篇念头",
            "tone": _single_line(payload.get("tone"), 40)
            or runtime_persona_setting(self, "default_style", "温柔"),
            "point_of_view": _single_line(payload.get("point_of_view"), 30) or "第三人称有限视角",
            "point_of_view_policy_version": 2,
            "source": source.get("source") or "life",
            "source_text": source_text,
            "target_chars": target_chars,
            "current_chars": 0,
            "status": "drafting",
            "draft_chunks": [],
            "disclosed_milestones": [],
            "story_bible": story_bible,
            "creative_memory_pool": [],
            "outline": [],
            "characters": [],
            "revision_notes": [],
            "quality_reviews": [],
            "manual_edits": [],
            "last_manual_edit_at": 0,
            "last_manual_edit_summary": "",
            "next_hint": _single_line(payload.get("next_hint"), 120) or "先写一个很小的开场画面",
            "created_at": now,
            "last_advanced_at": now,
            "last_share_at": 0,
            "share_count": 0,
        }
        project["next_advance_at"] = now + self._creative_advance_gap_minutes(project, now, initial=True) * 60
        return project

    # ============================================================
    # Outline Generation
    # ============================================================

    async def _generate_outline_for_chunk(
        self, project: dict[str, Any], story_bible: dict[str, Any],
        memories: list[dict[str, Any]], budget: int,
    ) -> str:
        memory_ctx = "\n".join(
            f"- [{_single_line(m.get('type'), 16)}] {_single_line(m.get('content'), 120)}"
            for m in memories if isinstance(m, dict)
        )
        recent_outlines = "\n".join(
            _single_line(o, 140) for o in story_bible.get("recent_outlines", [])[-3:]
            if _single_line(o, 140)
        )
        manual_outline_ctx = self._creative_manual_outline_context(project)
        character_ctx = self._creative_character_context(project)
        revision_ctx = self._creative_manual_revision_context(project)
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        companion_memory_ctx = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                companion_memory_ctx = await composer(
                    kind="creative_outline",
                    query=(
                        f"私下创作大纲：{_single_line(project.get('title'), 40)}；"
                        f"{_single_line(project.get('premise'), 160)}；"
                        "用户修订、角色设定、上次写到哪里、创作偏好、避雷、连续性"
                    ),
                    top_k=5,
                    max_chars=850,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创作大纲 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
你在为一个私人创作项目安排本次要写的一小段,先给出简短大纲。

作品类型：{self._creative_work_type(project)}
标题：{_single_line(project.get('title'), 40)}
核心设定：{_single_line(project.get('premise'), 180)}
当前主线：{_single_line(story_bible.get('mainline_direction'), 140)}
故事内时间：{_single_line(story_bible.get('story_time'), 60) or '尚未确定,本段应顺手定下一个具体时刻。'}
人工维护大纲（优先级最高,不能推翻）：
{manual_outline_ctx or '暂无人工大纲。'}
角色表（优先级高,不要擅自改名或改设定）：
{character_ctx or '暂无角色表。'}
人工修订约束：
{revision_ctx or '暂无人工修订。'}
用户配置的创作方向：
{direction_prompt or '未指定。'}
活跃主题：{', '.join(_single_line(t, 18) for t in story_bible.get('active_themes', []) if _single_line(t, 18)) or '暂无'}
未解决线索：{', '.join(_single_line(t, 24) for t in story_bible.get('unresolved_threads', []) if _single_line(t, 24)) or '暂无'}
下一步方向：{_single_line(story_bible.get('next_direction') or project.get('next_hint'), 140)}
最近大纲（避免重复套路）：
{recent_outlines or '暂无'}
相关记忆：
{memory_ctx or '暂无相关记忆。'}
我会牢牢记住你 项目参考：
{companion_memory_ctx or '暂无外部项目参考。'}

要求：
1. 输出 3 到 5 条短项目符号,每条不超过 22 字。
2. 第一条必须写明本段的时间处理：接上一段继续,还是推进到故事内的下一个时刻（写清推进到什么时候）。
3. 本段必须推进至少一个叙事元素,不要原地踏步；也不要每段都靠时间跳跃推进,同一场景内可以有动作进展。
4. 如果人工大纲/角色表/人工修订存在,本次大纲必须顺着它们走。
5. 不要解释,不要写正文,不要 JSON。
6. 本次字数预算大约 {budget} 字。
""".strip()
        text = await self._llm_call(
            prompt, max_tokens=200,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "CREATIVE_OUTLINE_PROVIDER_ID", "creative_outline_provider_id", "creative"
                ),
                _persona_provider_id(self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="creative_outline",
        )
        outline = self._normalize_outline_text(text)
        if outline:
            ro = story_bible.get("recent_outlines")
            if not isinstance(ro, list):
                ro = []
            ro.append(outline)
            story_bible["recent_outlines"] = ro[-6:]
        return outline

    # ============================================================
    # Quality Review
    # ============================================================

    async def _review_creative_chunk(
        self, project: dict[str, Any], story_bible: dict[str, Any],
        outline: str, chunk_text: str, recent_chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not chunk_text:
            return {"passed": False, "rewrite_focus": "片段为空，重新写一段具体正文。"}
        recent_digest = self._creative_recent_chunk_digest(recent_chunks, limit=4)
        manual_outline_ctx = self._creative_manual_outline_context(project)
        character_ctx = self._creative_character_context(project)
        revision_ctx = self._creative_manual_revision_context(project)
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        companion_memory_ctx = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                companion_memory_ctx = await composer(
                    kind="creative_review",
                    query=(
                        f"私下创作审稿：{_single_line(project.get('title'), 40)}；"
                        "人格边界、用户修订、重复问题、长期项目连续性、角色关系、避雷"
                    ),
                    top_k=5,
                    max_chars=850,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创作审稿 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
你是一个严格但懂文风的审稿人。检查这段私人创作片段是否满足：贴合人格、推进作品、避免重复。

作者人格：{self._creative_persona_style_context()}
作品类型：{self._creative_work_type(project)}
核心设定：{_single_line(project.get('premise'), 160)}
当前主线：{_single_line(story_bible.get('mainline_direction'), 140)}
故事内时间（写这段之前）：{_single_line(story_bible.get('story_time'), 60) or '尚未确定'}
未解决线索：{', '.join(_single_line(t, 20) for t in story_bible.get('unresolved_threads', []) if _single_line(t, 20)) or '暂无'}
人工维护大纲：
{manual_outline_ctx or '暂无人工大纲。'}
角色表：
{character_ctx or '暂无角色表。'}
人工修订约束：
{revision_ctx or '暂无人工修订。'}
用户配置的创作方向：
{direction_prompt or '未指定。'}
当前大纲：
{outline or '暂无大纲'}
最近片段摘要：
{recent_digest}
我会牢牢记住你 项目参考：
{companion_memory_ctx or '暂无外部项目参考。'}

待审片段：
{chunk_text}

检查重点：
1. 是否像插件设定的人格会写出来的东西，不要越过身份、年龄、经验边界。
2. 是否真的推进了内容，而不是空转、堆辞藻、重复意象。
3. 是否出现反复抒情、重复句式、重复画面、重复心理活动。
4. 是否和最近几段太像。
5. 是否违背人工维护的大纲、角色表、人工修订或用户配置的创作方向。
6. 文风是否自然、像人写的：排比堆叠、四字词连用、"感到一阵X"式直陈情绪、比喻过密、结尾升华总结或"或许……吧"式感慨收束，都算 AI 味。
7. 故事内时间是否顺着推进：无故倒流、或连续几段原地停在同一个时刻都算问题。

只输出 JSON：
{{
  "passed": true,
  "persona_score": 0,
  "progress_score": 0,
  "repetition_score": 0,
  "style_score": 0,
  "issues": ["问题"],
  "rewrite_focus": "如果需要重写，用一句话说清楚怎么改"
}}
""".strip()
        text = await self._llm_call(
            prompt, max_tokens=220,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "CREATIVE_REVIEW_PROVIDER_ID", "creative_review_provider_id", "creative"
                ),
                _persona_provider_id(self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="creative_review",
        )
        payload = self._extract_json_payload(text or "")
        return payload if isinstance(payload, dict) else {}

    # ============================================================
    # Post-Generation Extraction
    # ============================================================

    async def _post_generation_extract(
        self, project: dict[str, Any], story_bible: dict[str, Any],
        new_chunk_text: str, chunk_index: int,
    ) -> dict[str, Any]:
        manual_outline_ctx = self._creative_manual_outline_context(project)
        character_ctx = self._creative_character_context(project)
        revision_ctx = self._creative_manual_revision_context(project)
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        companion_memory_ctx = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                companion_memory_ctx = await composer(
                    kind="creative_extract",
                    query=(
                        f"私下创作抽取：{_single_line(project.get('title'), 40)}；"
                        "需要长期记住的角色、线索、用户修订、下一步、避雷"
                    ),
                    top_k=4,
                    max_chars=700,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创作抽取 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
整理一个长期创作项目刚写出的新片段,提取对后续续写最有用的结构化信息。

当前主线：{_single_line(story_bible.get('mainline_direction'), 140)}
未解决线索：{', '.join(_single_line(t, 24) for t in story_bible.get('unresolved_threads', []) if _single_line(t, 24)) or '暂无'}
下一步方向：{_single_line(story_bible.get('next_direction') or project.get('next_hint'), 140)}
故事内时间（写新片段之前）：{_single_line(story_bible.get('story_time'), 60) or '尚未确定'}
人工维护大纲：
{manual_outline_ctx or '暂无人工大纲。'}
角色表：
{character_ctx or '暂无角色表。'}
人工修订约束：
{revision_ctx or '暂无人工修订。'}
用户配置的创作方向：
{direction_prompt or '未指定。'}
我会牢牢记住你 项目参考：
{companion_memory_ctx or '暂无外部项目参考。'}
新片段：{_single_line(new_chunk_text, 420)}

输出 JSON：
{{
  "mainline_direction": "一句话概括现在真正推进到哪条主线",
  "themes_used": ["最多3个主题词"],
  "threads_advanced": ["最多2条推进中的线索"],
  "threads_resolved": ["最多1条已收束线索"],
  "new_threads": ["最多2条新埋下的线索"],
  "important_facts": ["最多3条后续必须记住的事实"],
  "keywords": ["最多6个关键词"],
  "story_time": "写完这个片段后,故事内现在处于什么时刻,10到24字",
  "next_direction": "一句话描述下一段最自然该写什么"
}}
""".strip()
        text = await self._llm_call(
            prompt, max_tokens=300,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "CREATIVE_REVIEW_PROVIDER_ID", "creative_review_provider_id", "creative"
                ),
                _persona_provider_id(self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="creative_extract",
        )
        payload = self._extract_json_payload(text or "")
        if not isinstance(payload, dict):
            return {}

        def _limit(values: Any, size: int, width: int) -> list[str]:
            if not isinstance(values, list):
                return []
            result: list[str] = []
            for v in values:
                s = _single_line(v, width)
                if s and s not in result:
                    result.append(s)
                if len(result) >= size:
                    break
            return result

        themes_used = _limit(payload.get("themes_used"), 3, 20)
        threads_advanced = _limit(payload.get("threads_advanced"), 2, 40)
        threads_resolved = _limit(payload.get("threads_resolved"), 1, 40)
        new_threads = _limit(payload.get("new_threads"), 2, 40)
        important_facts = _limit(payload.get("important_facts"), 3, 60)
        keywords = _limit(payload.get("keywords"), 6, 16)
        mainline_direction = _single_line(payload.get("mainline_direction"), 140)
        next_direction = _single_line(payload.get("next_direction"), 140)
        story_time = _single_line(payload.get("story_time"), 60)

        active_themes = [t for t in story_bible.get("active_themes", []) if _single_line(t, 20)]
        unresolved = [t for t in story_bible.get("unresolved_threads", []) if _single_line(t, 40)]
        resolved = [t for t in story_bible.get("resolved_threads", []) if _single_line(t, 40)]
        imp_facts = [t for t in story_bible.get("important_facts", []) if _single_line(t, 60)]
        recent_kw = [t for t in story_bible.get("recent_keywords", []) if _single_line(t, 16)]

        for t in themes_used:
            if t not in active_themes:
                active_themes.append(t)
        for t in threads_resolved:
            if t not in resolved:
                resolved.append(t)
            unresolved = [u for u in unresolved if u != t]
        for t in threads_advanced + new_threads:
            if t and t not in unresolved and t not in resolved:
                unresolved.append(t)
        for k in keywords:
            if k not in recent_kw:
                recent_kw.append(k)
        for f in important_facts:
            if f not in imp_facts:
                imp_facts.append(f)

        if mainline_direction:
            story_bible["mainline_direction"] = mainline_direction
        story_bible["active_themes"] = active_themes[-6:]
        story_bible["resolved_threads"] = resolved[-20:]
        story_bible["unresolved_threads"] = unresolved[-12:]
        story_bible["important_facts"] = imp_facts[-12:]
        if next_direction:
            story_bible["next_direction"] = next_direction
        if story_time:
            story_bible["story_time"] = story_time
        story_bible["recent_keywords"] = recent_kw[-20:]
        story_bible["last_updated_chunk"] = _safe_int(chunk_index, chunk_index, 0)
        return {
            "themes_used": themes_used,
            "threads_advanced": threads_advanced,
            "threads_resolved": threads_resolved,
            "new_threads": new_threads,
            "important_facts": important_facts,
            "keywords": keywords,
            "mainline_direction": mainline_direction,
            "next_direction": next_direction,
            "story_time": story_time,
        }

    # ============================================================
    # Manual Edit
    # ============================================================

    async def _apply_creative_manual_edit(
        self, project_id: str, edit_type: str, edit_content: str,
        edit_title: str = "", chunk_index: int = -1,
    ) -> dict[str, Any]:
        async with self._data_lock:
            projects = self._creative_projects()
            project = next((p for p in projects if p.get("id") == project_id), None)
            if not project:
                return {"success": False, "error": "作品不存在"}
            now = _now_ts()
            normalized_type = _single_line(edit_type, 24)
            normalized_title = _single_line(edit_title or normalized_type, 60)
            if normalized_type == "chunk_text":
                chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
                if not (0 <= chunk_index < len(chunks)) or not isinstance(chunks[chunk_index], dict):
                    return {"success": False, "error": "片段索引越界"}
            edit_record = {
                "id": uuid.uuid4().hex[:12],
                "type": normalized_type,
                "title": normalized_title,
                "content": _single_line(edit_content, 2000),
                "chunk_index": chunk_index,
                "created_at": now,
            }
            edits = project.setdefault("manual_edits", [])
            if not isinstance(edits, list):
                edits = []
                project["manual_edits"] = edits
            edits.append(edit_record)
            del edits[:-CREATIVE_MAX_REVISION_HISTORY]
            project["last_manual_edit_at"] = now
            project["last_manual_edit_summary"] = _single_line(edit_title or edit_type, 120)
            story_bible = self._get_or_create_story_bible(project)
            pool = self._get_or_create_memory_pool(project)
            if normalized_type == "chunk_text":
                chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
                chunks[chunk_index]["text"] = _single_line(edit_content, 5000)
                chunks[chunk_index]["chars"] = len(chunks[chunk_index]["text"])
                chunks[chunk_index]["manually_edited"] = True
                project["current_chars"] = sum(
                    _safe_int(c.get("chars"), 0, 0) for c in chunks if isinstance(c, dict)
                )
            elif normalized_type == "outline":
                lines = [_single_line(l, 100) for l in edit_content.split("\n") if _single_line(l, 100)]
                project["outline"] = lines[:30]
                if lines:
                    story_bible["next_direction"] = lines[0]
                    story_bible["recent_outlines"] = (story_bible.get("recent_outlines") or [])[-5:] + ["\n".join(f"- {line}" for line in lines[:5])]
            elif normalized_type == "premise":
                project["premise"] = _single_line(edit_content, 160)
                story_bible["mainline_direction"] = _single_line(edit_content, 160)
            elif normalized_type == "title":
                project["title"] = _single_line(edit_content, 40)
            elif normalized_type == "characters":
                try:
                    parsed = json.loads(edit_content) if edit_content.strip().startswith(("[", "{")) else []
                    if isinstance(parsed, dict):
                        parsed = parsed.get("characters") if isinstance(parsed.get("characters"), list) else []
                    if isinstance(parsed, list):
                        project["characters"] = [item for item in parsed if isinstance(item, dict)]
                except Exception:
                    pass
            elif normalized_type == "next_hint":
                project["next_hint"] = _single_line(edit_content, 160)
                story_bible["next_direction"] = _single_line(edit_content, 160)
            keywords = self._extract_creative_keywords(f"{normalized_title} {edit_content}", limit=8) or ["人工修订"]
            self._add_memory_entry(
                pool,
                project_id,
                "revision",
                f"{normalized_title or normalized_type}: {_single_line(edit_content, 180)}",
                keywords,
                importance=5,
            )
            self.data["creative_projects"] = projects
            self._save_data_sync(sections={"creative_projects"})
            return {"success": True, "project_id": project_id, "edit_type": normalized_type}

    async def _rebuild_creative_memory_from_project(self, project_id: str) -> dict[str, Any]:
        async with self._data_lock:
            projects = self._creative_projects()
            project = next((p for p in projects if p.get("id") == project_id), None)
            if not project:
                return {"success": False, "error": "作品不存在"}
            pool = self._get_or_create_memory_pool(project)
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            story_bible = self._get_or_create_story_bible(project)
            pool.clear()
            for chunk in chunks[-12:]:
                if not isinstance(chunk, dict):
                    continue
                text = _single_line(chunk.get("text"), 400)
                if text:
                    self._add_memory_entry(
                        pool, project_id, "scene", text,
                        story_bible.get("recent_keywords", []), importance=3,
                    )
            outline = project.get("outline") if isinstance(project.get("outline"), list) else []
            for line in outline[:12]:
                text = _single_line(line, 120)
                if text:
                    self._add_memory_entry(pool, project_id, "outline", text, self._extract_creative_keywords(text), importance=5)
            for character in self._get_project_characters(project)[:10]:
                name = _single_line(character.get("name"), 32)
                desc = _single_line(character.get("description") or character.get("personality") or character.get("background"), 160)
                if name or desc:
                    self._add_memory_entry(
                        pool,
                        project_id,
                        "character",
                        "｜".join(part for part in (name, desc) if part),
                        self._extract_creative_keywords(f"{name} {desc}"),
                        importance=5,
                    )
            edits = project.get("manual_edits") if isinstance(project.get("manual_edits"), list) else []
            for edit in edits[-CREATIVE_MAX_REVISION_HISTORY:]:
                if not isinstance(edit, dict):
                    continue
                title = _single_line(edit.get("title") or edit.get("type"), 60)
                content = _single_line(edit.get("content"), 180)
                if title or content:
                    self._add_memory_entry(
                        pool,
                        project_id,
                        "revision",
                        f"{title}: {content}",
                        self._extract_creative_keywords(f"{title} {content}") or ["人工修订"],
                        importance=5,
                    )
            if project.get("last_manual_edit_summary"):
                self._add_memory_entry(
                    pool, project_id, "revision",
                    f"最近人工修订: {_single_line(project.get('last_manual_edit_summary'), 120)}",
                    ["人工修订"], importance=5,
                )
            self.data["creative_projects"] = projects
            self._save_data_sync(sections={"creative_projects"})
            return {"success": True, "project_id": project_id, "memory_count": len(pool)}

    # ============================================================
    # Core Chunk Generation (with story_bible + outline + review)
    # ============================================================

    async def _generate_creative_chunk(self, project: dict[str, Any], budget: int) -> str:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        recent = "\n".join(_single_line((item or {}).get("text"), 240) for item in chunks[-3:] if isinstance(item, dict))
        recent_digest = self._creative_recent_chunk_digest(chunks, limit=5)
        remaining = _safe_int(project.get("target_chars"), 2400, 300, 5200) - _safe_int(project.get("current_chars"), 0, 0)
        finish_hint = "可以自然收束到一个小段落结尾,但不要完结全篇。" if remaining <= budget + 120 else "不要完结全篇,只推进一个很小的片段。"
        persona_context = self._creative_persona_style_context()
        work_type = self._creative_work_type(project)
        point_of_view = self._creative_point_of_view(project)
        output_rule = self._creative_work_output_rule(work_type, point_of_view)
        story_bible = self._get_or_create_story_bible(project)
        pool = self._get_or_create_memory_pool(project)
        memories = self._retrieve_relevant_memories(pool, str(project.get("id") or ""), story_bible.get("recent_keywords", []))
        bible_ctx = "\n".join(
            p for p in (
                f"当前主线：{_single_line(story_bible.get('mainline_direction'), 140)}" if _single_line(story_bible.get('mainline_direction'), 140) else "",
                f"活跃主题：{', '.join(_single_line(t, 18) for t in story_bible.get('active_themes', []) if _single_line(t, 18))}" if isinstance(story_bible.get("active_themes"), list) else "",
                f"未解决线索：{', '.join(_single_line(t, 24) for t in story_bible.get('unresolved_threads', []) if _single_line(t, 24))}" if isinstance(story_bible.get("unresolved_threads"), list) else "",
                f"已解决线索：{', '.join(_single_line(t, 24) for t in story_bible.get('resolved_threads', []) if _single_line(t, 24))}" if isinstance(story_bible.get("resolved_threads"), list) else "",
                f"必须记住的事实：{', '.join(_single_line(t, 24) for t in story_bible.get('important_facts', []) if _single_line(t, 24))}" if isinstance(story_bible.get("important_facts"), list) else "",
                f"下一步方向：{_single_line(story_bible.get('next_direction'), 140)}" if _single_line(story_bible.get("next_direction"), 140) else "",
            ) if p
        )
        memory_ctx = "\n".join(
            f"- [{_single_line(m.get('type'), 16)}] {_single_line(m.get('content'), 120)}"
            for m in memories if isinstance(m, dict)
        )
        manual_outline_ctx = self._creative_manual_outline_context(project)
        character_ctx = self._creative_character_context(project)
        revision_ctx = self._creative_manual_revision_context(project)
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        outline = await self._generate_outline_for_chunk(project, story_bible, memories, budget)
        companion_memory_ctx = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                companion_memory_ctx = await composer(
                    kind="creative_writing",
                    query=(
                        f"私下创作续写：{_single_line(project.get('title'), 40)}；"
                        f"{_single_line(project.get('premise'), 160)}；"
                        "上次写到哪里、用户修订、角色设定、项目连续性、喜欢的文风、避雷"
                    ),
                    top_k=6,
                    max_chars=1000,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创作续写 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))

        async def _do_generate(extra_notice: str = "") -> str:
            story_time = _single_line(story_bible.get("story_time"), 60)
            prompt = f"""
你就是下面这个人格,此刻正在闲暇时写自己想写的作品。请只写本次随手能写下的一小段。

【作者人格与身份】
{persona_context}

作品类型：{work_type}
标题：{_single_line(project.get("title"), 40)}
核心设定：{_single_line(project.get("premise"), 180)}
行文气质：{_single_line(project.get("tone"), 60)}
叙事视角：{point_of_view}
灵感来源：{_single_line(project.get("source_text"), 180)}
故事内时间：{story_time or "尚未确定,本段可以顺手定下一个具体时刻。"}
项目结构：{bible_ctx or '先顺着刚萌生的主线推进。'}
人工维护大纲（优先级高于本段临时大纲）：
{manual_outline_ctx or '暂无人工大纲。'}
角色表（优先级高,不要擅自改名、换关系或改设定）：
{character_ctx or '暂无角色表。'}
人工修订约束：
{revision_ctx or '暂无人工修订。'}
用户配置的创作方向：
{direction_prompt or '未指定。'}
相关记忆：
{memory_ctx or '暂无。'}
我会牢牢记住你 项目参考：
{companion_memory_ctx or '暂无外部项目参考。'}
最近片段摘要：
{recent_digest}
上一段：{recent or "还没有正文。"}
下一步念头：{_single_line(project.get("next_hint"), 140)}
本段大纲：{outline or '先写一个具体画面,并推进一条线索。'}

本次字数上限：{budget} 个中文字符左右,写完一个自然的小节就停。
要求：
1. {output_rule} 不要标题、说明、JSON、系统旁白或"下面是"。
2. 文风要像这个人格本人写的：从人格日常在意的事物里取细节,人格关注什么,笔下的名词就落在什么上；用词、观察角度、人物成熟度、知识范围都不能越过人设。
3. 写作工艺（重要,逐条自查）：
   - 连续两句不要用相同结构开头,长短句交替,允许不完整的短句；
   - 全段最多一个明喻,"像/仿佛/宛如"只允许出现一次,且必须贴切；
   - 段落收尾停在动作、对话或一个感官细节上,禁止总结句、升华句和"或许……吧"式感慨收束；
   - 情绪用动作和感官呈现,不写"感到一阵X""心里涌起Y"这类直陈情绪；
   - 用带时间、地点、物件细节的具体名词,不用"时光""岁月""心底""灵魂"式空词,不堆四字词。
4. 作者人格影响文风,但作者不等于必须出现在作品里；不要把所有作品都写成 Bot 的日记或对用户的自白。
5. 顺着故事内时间写：本段接续"故事内时间"或自然向前推进；有意的倒叙、闪回要在文内自然交代,不能无声倒流,也不要原地停在同一个时刻。
6. {finish_hint} 本段至少推进一个叙事元素,不能只是换皮重复前文；严格参考本段大纲,但要写得自然,不是提纲照抄。如果人工维护大纲、角色表或人工修订存在,必须优先服从；同时遵守用户配置的创作方向。
{extra_notice}
""".strip()
            text = await self._llm_call(
                prompt, max_tokens=max(220, budget + 160),
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"
                    ),
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                ),
                task="creative_writing",
            )
            cleaned = str(text or "").strip()
            cleaned = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
            cleaned = re.sub(r"^(?:正文|续写|片段)[:：]\s*", "", cleaned).strip()
            if len(cleaned) > budget + 80:
                cleaned = cleaned[: budget + 80].rstrip("，,、；;：:")
                if cleaned and cleaned[-1] not in "。！？…":
                    cleaned += "。"
            return cleaned

        extra_notice = ""
        for attempt in range(CREATIVE_SIMILARITY_RETRIES + 1):
            cleaned = await _do_generate(extra_notice)
            similarity_hit = self._check_chunk_similarity(cleaned, chunks[-12:])
            review = await self._review_creative_chunk(project, story_bible, outline, cleaned, chunks[-5:])
            ps = _safe_int(review.get("persona_score"), 8, 0, 10) if isinstance(review, dict) else 8
            prs = _safe_int(review.get("progress_score"), 8, 0, 10) if isinstance(review, dict) else 8
            rs = _safe_int(review.get("repetition_score"), 8, 0, 10) if isinstance(review, dict) else 8
            # 兼容未返回 style_score 的旧版审校输出：缺省按通过档计。
            ss = _safe_int(review.get("style_score"), 8, 0, 10) if isinstance(review, dict) else 8
            passed = bool(review.get("passed", True)) if isinstance(review, dict) else True
            focus = _single_line(review.get("rewrite_focus"), 140) if isinstance(review, dict) else ""
            if (
                cleaned
                and not similarity_hit
                and passed
                and ps >= CREATIVE_REVIEW_MIN_SCORE
                and prs >= CREATIVE_REVIEW_MIN_SCORE
                and rs >= CREATIVE_REVIEW_MIN_SCORE
                and ss >= CREATIVE_REVIEW_MIN_SCORE
            ):
                return cleaned
            notes: list[str] = []
            if not cleaned:
                notes.append("生成结果为空，必须重新写出实际正文。")
            if similarity_hit:
                notes.append("避免与最近片段重复的意象、句式和情节走向。")
            if focus:
                notes.append(focus)
            if ps < CREATIVE_REVIEW_MIN_SCORE:
                notes.append("更贴近插件指定人格，不要突然成熟、说教、越过身份经验。")
            if prs < CREATIVE_REVIEW_MIN_SCORE:
                notes.append("必须真正推进一条线索，不要空转抒情。")
            if rs < CREATIVE_REVIEW_MIN_SCORE:
                notes.append("减少重复，不要反复写相同心理和画面。")
            if ss < CREATIVE_REVIEW_MIN_SCORE:
                notes.append("去掉AI味：删排比和四字词堆叠，砍掉多余比喻，情绪改用动作和感官细节呈现，结尾停在动作或细节上，不要总结升华。")
            extra_notice = "注意重写：" + " ".join(notes)
        return ""

    def _defer_creative_project_advance(
        self,
        project: dict[str, Any],
        *,
        now: float,
        reason: str,
    ) -> int:
        failures = _safe_int(project.get("advance_failure_count"), 0, 0) + 1
        delay_minutes = min(360, 30 * (2 ** min(failures - 1, 4)))
        project["advance_failure_count"] = failures
        project["last_advance_failed_at"] = now
        project["last_advance_error"] = _single_line(reason, 180) or "创作片段未通过质量检查"
        project["next_advance_at"] = now + delay_minutes * 60
        return delay_minutes

    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        if not runtime_persona_setting(self, "enable_creative_writing", True):
            return False
        if not idle_checked and not self._bot_currently_idle_for_creative_writing():
            return False
        projects = self._creative_projects()
        active = [item for item in projects if item.get("status") == "drafting"]
        now = _now_ts()
        if len(active) >= runtime_persona_setting(self, "creative_max_active_projects", 2):
            return False
        last_created = max((_safe_float(item.get("created_at"), 0) for item in projects), default=0)
        if now - last_created < 10 * 3600:
            return False
        if random.random() > runtime_persona_setting(self, "creative_inspiration_probability", 0.2):
            return False
        source = self._creative_inspiration_source()
        if not source:
            return False
        project = await self._generate_creative_project(source)
        if not project:
            return False
        async with self._data_lock:
            projects = self._creative_projects()
            active = [item for item in projects if item.get("status") == "drafting"]
            now = _now_ts()
            if len(active) >= runtime_persona_setting(self, "creative_max_active_projects", 2):
                return False
            last_created = max((_safe_float(item.get("created_at"), 0) for item in projects), default=0)
            if now - last_created < 10 * 3600:
                return False
            if self._inspiration_already_used(source.get("text", ""), active):
                return False
            projects.append(project)
            del projects[:-20]
            self.data["creative_projects"] = projects
            self._save_data_sync(sections={"creative_projects"})
        logger.info("[PrivateCompanion] 新增创作项目: %s / %s", project.get("work_type"), project.get("title"))
        return True

    @staticmethod
    def _creative_cover_file_exists(project: dict[str, Any]) -> bool:
        path_text = _path_text(project.get("cover_path"), 1000)
        if not path_text:
            return False
        try:
            path = Path(path_text)
            return path.exists() and path.is_file()
        except (OSError, ValueError):
            return False

    def _creative_cover_person_reference_configured(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return False
        getter = getattr(self, "_photo_persona_reference_image_path", None)
        if callable(getter):
            try:
                if _single_line(getter(), 500):
                    return True
            except Exception:
                pass
        return bool(
            _single_line(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        )

    def _creative_cover_needs_identity_upgrade(self, project: dict[str, Any]) -> bool:
        if not self._creative_cover_file_exists(project):
            return False
        if _safe_float(project.get("cover_generated_at"), 0) <= 0:
            return False
        if _single_line(project.get("cover_generation_person_policy"), 40) == "single_reference_character":
            return False
        return bool(
            self._creative_cover_has_person_subject(project)
            and self._creative_cover_person_reference_configured()
        )

    def _creative_cover_candidate_id(self) -> str:
        if not bool(runtime_persona_setting(self, "enable_creative_cover_generation", False)):
            return ""
        now = _now_ts()
        for project in reversed(self._creative_projects()):
            if not isinstance(project, dict):
                continue
            if self._creative_cover_file_exists(project) and not self._creative_cover_needs_identity_upgrade(project):
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not any(isinstance(chunk, dict) and _single_line(chunk.get("text"), 80) for chunk in chunks):
                continue
            attempts = _safe_int(project.get("cover_generation_attempts"), 0, 0)
            if attempts >= 3:
                continue
            if now < _safe_float(project.get("cover_generation_next_retry_at"), 0):
                continue
            if (
                _single_line(project.get("cover_generation_status"), 24) == "generating"
                and now - _safe_float(project.get("cover_generation_attempted_at"), 0) < 20 * 60
            ):
                continue
            project_id = _single_line(project.get("id"), 32)
            if project_id:
                return project_id
        return ""

    @staticmethod
    def _creative_cover_style_instruction(project: dict[str, Any]) -> tuple[str, str]:
        source = re.sub(
            r"\s+",
            "",
            " ".join(
                str(project.get(key) or "")
                for key in ("work_type", "tone", "premise", "source_text")
            ).lower(),
        )
        style_rules = (
            (
                ("古风", "武侠", "仙侠", "历史", "志怪", "水墨"),
                "东方水墨",
                "contemporary Chinese ink illustration with restrained mineral pigments, expressive brush texture, elegant negative space",
            ),
            (
                ("赛博", "cyberpunk", "霓虹", "朋克"),
                "赛博视觉",
                "refined cyberpunk editorial illustration, luminous neon accents, deep urban atmosphere, crisp graphic shapes",
            ),
            (
                ("悬疑", "推理", "惊悚", "犯罪", "侦探", "谜"),
                "悬疑黑色电影",
                "restrained neo-noir editorial illustration, high-contrast lighting, controlled shadows, limited color palette, subtle visual clue",
            ),
            (
                ("恐怖", "怪谈", "诡异", "克苏鲁", "gothic"),
                "哥特暗黑",
                "atmospheric gothic illustration, unsettling quiet, tactile shadows, muted colors, elegant rather than graphic horror",
            ),
            (
                ("科幻", "未来", "宇宙", "星际", "机器人", "人工智能", "奇幻", "魔法", "幻想"),
                "幻想概念插画",
                "cinematic speculative-fiction concept illustration, immersive worldbuilding, dramatic scale, sophisticated atmospheric color",
            ),
            (
                ("诗", "诗歌", "散文", "随笔", "札记", "日记", "意识流"),
                "诗意水彩",
                "poetic editorial watercolor and printmaking, tactile paper texture, symbolic imagery, soft layered color, generous negative space",
            ),
            (
                ("童话", "儿童", "寓言", "轻小说", "校园", "青春"),
                "清新叙事插画",
                "polished narrative illustration with clean shapes, lively color rhythm, gentle detail, contemporary Japanese editorial sensibility",
            ),
            (
                ("喜剧", "搞笑", "幽默", "轻松", "荒诞"),
                "轻快平面插画",
                "playful contemporary graphic illustration, bold readable shapes, witty visual metaphor, bright but balanced color palette",
            ),
            (
                ("治愈", "日常", "生活", "温柔", "恋爱", "爱情", "亲情", "成长"),
                "暖色文学插画",
                "warm literary editorial illustration, natural light, intimate everyday detail, painterly gouache texture, emotionally restrained composition",
            ),
        )
        for keywords, label, instruction in style_rules:
            if any(keyword in source for keyword in keywords):
                return label, instruction
        return (
            "文学编辑插画",
            "sophisticated literary editorial illustration, painterly texture, symbolic central image, restrained color harmony, timeless book-cover composition",
        )

    def _creative_cover_has_person_subject(self, project: dict[str, Any]) -> bool:
        if self._get_project_characters(project):
            return True
        source = " ".join(
            str(project.get(key) or "")
            for key in ("premise", "source_text", "tone", "work_type")
        )
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        source += " " + " ".join(
            str(chunk.get("text") or "")
            for chunk in chunks[-3:]
            if isinstance(chunk, dict)
        )
        return bool(
            re.search(
                r"(少女|女孩|女生|女性|女人|男孩|男生|男性|男人|少年|青年|老人|孩子|人物|角色|主角|主人公|"
                r"姑娘|先生|女士|母亲|父亲|妈妈|爸爸|姐姐|妹妹|哥哥|弟弟|她|他|人影|背影|侧脸|面孔|脸庞)",
                source,
            )
        )

    def _creative_cover_prompt_format_mode(self, value: Any = "") -> str:
        raw_value = value if str(value or "").strip() else runtime_persona_setting(
            self, "photo_generation_prompt_format", "traditional"
        )
        format_normalizer = getattr(
            self,
            "_normalize_photo_generation_prompt_format",
            None,
        )
        if callable(format_normalizer):
            normalized = str(format_normalizer(raw_value) or "traditional")
            return (
                normalized
                if normalized in {"traditional", "natural_language", "nai"}
                else "traditional"
            )
        raw_format = str(raw_value or "traditional").strip().lower()
        return (
            "natural_language"
            if raw_format in {"natural", "natural_language", "自然语言", "自然语言描述"}
            else "traditional"
        )

    def _creative_cover_prompt(
        self,
        project: dict[str, Any],
        *,
        person_reference_available: bool = False,
        prompt_format: str = "",
    ) -> str:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        recent_text = " ".join(
            _single_line(chunk.get("text"), 220)
            for chunk in chunks[-3:]
            if isinstance(chunk, dict) and _single_line(chunk.get("text"), 220)
        )
        characters = self._get_project_characters(project)
        primary_character = characters[0] if characters and isinstance(characters[0], dict) else {}
        character_hint = " - ".join(
            part
            for part in (
                _single_line(primary_character.get("name"), 30),
                _single_line(
                    primary_character.get("description") or primary_character.get("personality") or primary_character.get("role"),
                    100,
                ),
            )
            if part
        )
        visual_source = _single_line(
            " ".join(
                part
                for part in (
                    _single_line(project.get("premise"), 280),
                    _single_line(project.get("tone"), 80),
                    _single_line(project.get("source_text"), 180),
                    recent_text,
                )
                if part
            ),
            900,
        )
        _style_label, style_instruction = self._creative_cover_style_instruction(project)
        has_person_subject = self._creative_cover_has_person_subject(project)
        if has_person_subject and person_reference_available:
            subject_instruction = (
                "The supplied reference image defines the only visible character's identity and appearance. "
                "Show exactly one person in the entire artwork and preserve that reference identity. "
                "Do not show any second person, companion, man, woman, face, portrait, silhouette, shadow-person, "
                "human reflection, figure inside a mirror/window/portal/screen, framed photo, statue, or background crowd. "
                "Represent every other story character only through objects, architecture, light, weather, or abstract symbols. "
            )
        elif has_person_subject:
            subject_instruction = (
                "No character reference image is available, so do not depict any person or human-like character. "
                "Use a symbolic object-and-environment composition for the protagonist instead: no people, faces, bodies, "
                "silhouettes, portraits, human reflections, figures in mirrors/windows/portals/screens, framed photos, statues, or crowds. "
            )
        else:
            subject_instruction = (
                "Use a people-free symbolic composition: no people, faces, bodies, silhouettes, portraits, human reflections, "
                "figures in mirrors/windows/portals/screens, framed photos, statues, or crowds. "
            )
        prompt_format = self._creative_cover_prompt_format_mode(prompt_format)
        if prompt_format != "natural_language":
            positive_parts = [
                "polished book cover illustration",
                "vertical 2:3 composition",
                f"genre: {_single_line(project.get('work_type'), 50) or 'fiction'}",
                f"story motifs: {visual_source or 'an intimate original story with a clear central visual symbol'}",
                style_instruction,
                "one focused scene",
                "strong readable silhouette",
                "layered depth",
                "deliberate lighting",
                "quiet title-safe negative space",
            ]
            if has_person_subject and person_reference_available:
                positive_parts.extend(
                    [
                        "exactly one visible person",
                        "same identity and appearance as the supplied reference image",
                        f"primary character: {character_hint}" if character_hint else "single reference character",
                    ]
                )
            else:
                positive_parts.extend(["people-free composition", "symbolic objects and environment"])
            negative_parts = [
                "readable text",
                "letters",
                "typography",
                "logo",
                "watermark",
                "mockup",
                "border",
                "second person",
                "companion character",
                "multiple people",
                "crowd",
                "extra face",
                "human silhouette",
                "human reflection",
                "person in mirror",
                "person in window",
                "person in portal",
                "person on screen",
                "person in framed photo",
                "human statue",
                "shadow person",
            ]
            if not (has_person_subject and person_reference_available):
                negative_parts.extend(["person", "human face", "human body", "portrait"])
            return _single_line(
                "Positive prompt: "
                + ", ".join(_single_line(part, 900) for part in positive_parts if _single_line(part, 900))
                + ". Negative prompt: "
                + ", ".join(negative_parts)
                + ".",
                1800,
            )
        return _single_line(
            "Create a polished book cover illustration with a vertical 2:3 composition. "
            f"Genre: {_single_line(project.get('work_type'), 50) or 'fiction'}. "
            f"Story and visual motifs: {visual_source or 'an intimate original story with a clear central visual symbol'}. "
            + (f"Primary character only: {character_hint}. " if character_hint and person_reference_available else "")
            + subject_instruction
            + f"Art direction: {style_instruction}. "
            + "Use one focused scene, strong silhouette, layered depth, deliberate lighting, and enough quiet space for a title overlay. "
            "Artwork only: no readable text, no letters, no typography, no logo, no watermark, no mockup, no border.",
            1800,
        )

    async def _store_creative_cover_image(self, project_id: str, image_path: str) -> str:
        source_text = _path_text(image_path, 1000)
        if not source_text:
            return ""
        try:
            source = Path(source_text).resolve()
            if not source.exists() or not source.is_file():
                return ""
            root = Path(str(getattr(self, "data_dir", "") or ".")).resolve() / "creative_covers"
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
            target = root / f"{_single_line(project_id, 32)}{suffix}"
            if source != target.resolve():
                await asyncio.to_thread(shutil.copy2, source, target)
            return str(target)
        except Exception as exc:
            logger.warning("[PrivateCompanion] 创作封面保存失败: project=%s error=%s", project_id, _single_line(exc, 160))
            return ""

    async def _maybe_generate_creative_cover(self, project_id: str, *, force: bool = False) -> dict[str, Any] | None:
        if not force and not bool(
            runtime_persona_setting(self, "enable_creative_cover_generation", False)
        ):
            return None
        project_id = _single_line(project_id, 32)
        if not project_id:
            return None
        prompt_format = self._creative_cover_prompt_format_mode()
        locks = getattr(self, "_creative_cover_generation_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._creative_cover_generation_locks = locks
        lock = locks.get(project_id)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[project_id] = lock
        async with lock:
            async with self._data_lock:
                project = next(
                    (
                        item
                        for item in self._creative_projects()
                        if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id
                    ),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                identity_upgrade = self._creative_cover_needs_identity_upgrade(project)
                if not force and self._creative_cover_file_exists(project) and not identity_upgrade:
                    return dict(project)
                now = _now_ts()
                attempts = _safe_int(project.get("cover_generation_attempts"), 0, 0)
                if not force and (
                    attempts >= 3
                    or now < _safe_float(project.get("cover_generation_next_retry_at"), 0)
                ):
                    return dict(project)
                project_snapshot = deepcopy(project)

            generator = getattr(self, "_generate_photo_image", None)
            available = getattr(self, "_photo_text_available", None)
            if (
                not bool(runtime_persona_setting(self, "enable_photo_text_action", False))
                or not callable(generator)
                or (callable(available) and not available())
            ):
                async with self._data_lock:
                    project = next(
                        (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                        None,
                    )
                    if isinstance(project, dict):
                        project["cover_generation_status"] = "unavailable"
                        project["cover_generation_error"] = "当前没有可用的生图后端"
                        project["cover_generation_next_retry_at"] = _now_ts() + 3600
                        self._save_data_sync(sections={"creative_projects"})
                        return dict(project)
                return None

            has_person_subject = self._creative_cover_has_person_subject(project_snapshot)
            reference_image_path = ""
            if has_person_subject:
                reference_getter = getattr(self, "_photo_persona_reference_image_for_kind_async", None)
                if callable(reference_getter):
                    try:
                        reference_image_path = _path_text(
                            await reference_getter(
                                "portrait",
                                allow_daily_outfit=False,
                                request_text=json.dumps(project_snapshot, ensure_ascii=False),
                            ),
                            1000,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[PrivateCompanion] 创作封面读取人物参考图失败: project=%s error=%s",
                            project_id,
                            _single_line(exc, 160),
                        )
            if identity_upgrade and not reference_image_path:
                async with self._data_lock:
                    project = next(
                        (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                        None,
                    )
                    if isinstance(project, dict):
                        project["cover_generation_status"] = "ready"
                        project["cover_generation_error"] = "旧封面等待人物参考图可用后再升级"
                        project["cover_generation_next_retry_at"] = _now_ts() + 3 * 3600
                        self._save_data_sync(sections={"creative_projects"})
                        return dict(project)
                return None
            prompt_text = self._creative_cover_prompt(
                project_snapshot,
                person_reference_available=bool(reference_image_path),
                prompt_format=prompt_format,
            )
            style_label, _style_instruction = self._creative_cover_style_instruction(project_snapshot)
            attempt_number = attempts + 1
            async with self._data_lock:
                project = next(
                    (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                project["cover_generation_status"] = "generating"
                project["cover_generation_attempts"] = attempt_number
                project["cover_generation_attempted_at"] = _now_ts()
                project["cover_generation_error"] = ""
                self._save_data_sync(sections={"creative_projects"})

            backend, generated_path, note = await generator(
                workflow_kind="portrait" if reference_image_path else "text2img",
                prompt_text=prompt_text,
                session_key=f"creative_cover_{project_id}",
                reference_image_path=reference_image_path,
                image_size="",
                allow_daily_outfit_reference=False,
                prompt_format=prompt_format,
            )
            stored_path = await self._store_creative_cover_image(project_id, generated_path) if generated_path else ""
            now = _now_ts()
            async with self._data_lock:
                project = next(
                    (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                project["cover_generation_backend"] = _single_line(backend, 80)
                project["cover_generation_prompt"] = _single_line(prompt_text, 1800)
                project["cover_generation_style"] = _single_line(style_label, 40)
                project["cover_generation_reference_image"] = _path_text(reference_image_path, 1000)
                project["cover_generation_person_policy"] = (
                    "single_reference_character"
                    if reference_image_path
                    else ("symbolic_no_people" if has_person_subject else "no_people")
                )
                if stored_path:
                    project["cover_path"] = stored_path
                    project["cover_generated_at"] = now
                    project["cover_generation_status"] = "ready"
                    project["cover_generation_error"] = ""
                    project["cover_generation_next_retry_at"] = 0
                    logger.info("[PrivateCompanion] 创作封面已生成: project=%s backend=%s path=%s", project_id, _single_line(backend, 80), _single_line(stored_path, 180))
                else:
                    project["cover_generation_status"] = "failed"
                    project["cover_generation_error"] = _single_line(note, 220) or "生图失败"
                    project["cover_generation_next_retry_at"] = now + min(24, max(3, attempt_number * 3)) * 3600
                    logger.info("[PrivateCompanion] 创作封面未生成: project=%s attempt=%s error=%s", project_id, attempt_number, _single_line(note, 180))
                self._save_data_sync(sections={"creative_projects"})
                return dict(project)

    async def _maybe_advance_creative_projects(self) -> None:
        if not runtime_persona_setting(self, "enable_creative_writing", True):
            return
        if self._creative_has_pending_proactive_plan():
            return
        if not self._bot_currently_idle_for_creative_writing():
            return
        await self._maybe_start_creative_project(idle_checked=True)
        projects = self._creative_projects()
        now = _now_ts()
        changed = False
        creative_record_payload: tuple[dict[str, Any], str, dict[str, Any] | None] | None = None
        for project in projects:
            if project.get("status") != "drafting":
                continue
            if now < _safe_float(project.get("next_advance_at"), 0):
                continue
            budget = int(self._creative_chars_per_session() * random.uniform(0.72, 1.18))
            budget = max(60, min(1200, self._creative_chunk_budget_for(project, budget)))
            remaining = _safe_int(project.get("target_chars"), 2400, 300, 5200) - _safe_int(project.get("current_chars"), 0, 0)
            if remaining <= 0:
                project["status"] = "finished"
                changed = True
                continue
            try:
                chunk = await self._generate_creative_chunk(project, min(budget, max(70, remaining)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay_minutes = self._defer_creative_project_advance(
                    project,
                    now=now,
                    reason=f"创作生成失败: {_single_line(exc, 140)}",
                )
                logger.warning(
                    "[PrivateCompanion] 创作推进失败,已保留项目并退避: project=%s failures=%s delay=%sm error=%s",
                    _single_line(project.get("id"), 32),
                    _safe_int(project.get("advance_failure_count"), 1, 1),
                    delay_minutes,
                    _single_line(exc, 140),
                )
                changed = True
                break
            if not chunk:
                delay_minutes = self._defer_creative_project_advance(
                    project,
                    now=now,
                    reason="生成结果为空、重复或未通过质量复核",
                )
                logger.info(
                    "[PrivateCompanion] 创作片段未通过质量门,本轮不写入资料柜: project=%s failures=%s delay=%sm",
                    _single_line(project.get("id"), 32),
                    _safe_int(project.get("advance_failure_count"), 1, 1),
                    delay_minutes,
                )
                changed = True
                break
            chunks = project.setdefault("draft_chunks", [])
            if not isinstance(chunks, list):
                chunks = []
                project["draft_chunks"] = chunks
            chunks.append({
                "at": now,
                "text": chunk,
                "chars": len(chunk),
            })
            del chunks[:-40]
            story_bible = self._get_or_create_story_bible(project)
            extract = await self._post_generation_extract(project, story_bible, chunk, len(chunks))
            pool = self._get_or_create_memory_pool(project)
            self._add_memory_entry(pool, str(project.get("id") or ""), "scene", chunk, story_bible.get("recent_keywords", []), importance=3)
            if isinstance(extract, dict):
                new_threads = extract.get("new_threads") if isinstance(extract.get("new_threads"), list) else []
                important_facts = extract.get("important_facts") if isinstance(extract.get("important_facts"), list) else []
                if new_threads:
                    self._add_memory_entry(pool, str(project.get("id") or ""), "theme", f"新线索: {_single_line(new_threads[0], 120)}", [str(t) for t in new_threads[:3]], importance=4)
                if important_facts:
                    self._add_memory_entry(pool, str(project.get("id") or ""), "fact", f"必须记住: {_single_line(important_facts[0], 160)}", [str(f) for f in important_facts[:3]], importance=5)
                nd = _single_line(extract.get("next_direction"), 120)
                if nd:
                    project["next_hint"] = nd
            project["current_chars"] = _safe_int(project.get("current_chars"), 0, 0) + len(chunk)
            project["last_advanced_at"] = now
            project["next_advance_at"] = now + self._creative_advance_gap_minutes(project, now) * 60
            project["advance_failure_count"] = 0
            project.pop("last_advance_failed_at", None)
            project.pop("last_advance_error", None)
            if project["current_chars"] >= _safe_int(project.get("target_chars"), 2400, 300, 5200):
                project["status"] = "finished"
            creative_record_payload = (dict(project), chunk, extract if isinstance(extract, dict) else None)
            changed = True
            break
        self.data["creative_projects"] = projects
        async with self._data_lock:
            if self._maybe_schedule_creative_share():
                changed = True
            if changed:
                self._save_data_sync(sections={"creative_projects"})
        if creative_record_payload is not None:
            recorder = getattr(self, "_memory_companion_record_creative_progress", None)
            if callable(recorder):
                project_snapshot, chunk_snapshot, extract_snapshot = creative_record_payload
                await recorder(project=project_snapshot, chunk=chunk_snapshot, extract=extract_snapshot)
        cover_project_id = self._creative_cover_candidate_id()
        if cover_project_id:
            await self._maybe_generate_creative_cover(cover_project_id)

    def _latest_creative_share_candidate(self) -> dict[str, Any] | None:
        projects = self._creative_projects()
        for project in reversed(projects):
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not chunks:
                continue
            chunk = next((item for item in reversed(chunks) if isinstance(item, dict) and _single_line(item.get("text"), 260)), None)
            if not isinstance(chunk, dict):
                continue
            current_chars = _safe_int(project.get("current_chars"), 0, 0)
            target_chars = _safe_int(project.get("target_chars"), 2400, 300, 5200)
            story_bible = self._get_or_create_story_bible(project)
            snippet = _single_line(chunk.get("text"), 260)
            snippet_len = len(snippet)
            unresolved_threads = story_bible.get("unresolved_threads") if isinstance(story_bible.get("unresolved_threads"), list) else []
            important_facts = story_bible.get("important_facts") if isinstance(story_bible.get("important_facts"), list) else []
            chunk_count = len(chunks)
            completion_ratio = current_chars / max(1, target_chars)
            maturity_score = min(28.0, snippet_len / 6.5) + min(22.0, chunk_count * 5.0) + min(18.0, len(unresolved_threads) * 4.0) + min(16.0, len(important_facts) * 5.0) + min(16.0, completion_ratio * 24.0)
            disclosed = project.setdefault("disclosed_milestones", [])
            if not isinstance(disclosed, list):
                disclosed = []
                project["disclosed_milestones"] = disclosed
            milestone = ""
            disclosure_kind = "milestone"
            if project.get("status") == "finished" and "finished" not in disclosed:
                milestone = "finished"
            elif (
                current_chars >= max(180, int(target_chars * 0.14))
                and snippet_len >= 72 and maturity_score >= 36
                and "opening" not in disclosed
            ):
                milestone = "opening"
            elif (
                current_chars >= int(target_chars * 0.52)
                and snippet_len >= 88 and maturity_score >= 52
                and "midpoint" not in disclosed
            ):
                milestone = "midpoint"
            elif (
                current_chars >= max(520, int(target_chars * 0.3))
                and "impression_question" not in disclosed
                and chunk_count >= 3 and snippet_len >= 96 and maturity_score >= 58
                and random.random() < 0.28
            ):
                milestone = "impression_question"
                disclosure_kind = "ask_impression"
            if not milestone:
                continue
            return {
                "key": f"{project.get('id')}:{milestone}",
                "milestone": milestone,
                "disclosure_kind": disclosure_kind,
                "project_id": _single_line(project.get("id"), 20),
                "work_type": self._creative_work_type(project),
                "title": _single_line(project.get("title"), 40),
                "premise": _single_line(project.get("premise"), 140),
                "tone": _single_line(project.get("tone"), 40),
                "source": _single_line(project.get("source_text"), 140),
                "snippet": snippet,
                "current_chars": current_chars,
                "target_chars": target_chars,
                "chunk_count": chunk_count,
                "maturity_score": round(maturity_score, 2),
                "completion_ratio": round(completion_ratio, 4),
                "status": _single_line(project.get("status"), 24),
                "created_ts": _now_ts(),
            }
        return None

    def _mark_creative_milestone_disclosed(self, candidate: dict[str, Any]) -> None:
        project_id = _single_line(candidate.get("project_id"), 20)
        milestone = _single_line(candidate.get("milestone"), 40)
        if not project_id or not milestone:
            return
        for project in self._creative_projects():
            if _single_line(project.get("id"), 20) != project_id:
                continue
            disclosed = project.setdefault("disclosed_milestones", [])
            if not isinstance(disclosed, list):
                disclosed = []
                project["disclosed_milestones"] = disclosed
            if milestone not in disclosed:
                disclosed.append(milestone)
            break

    def _maybe_schedule_creative_share(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_creative_writing", True)):
            return False
        candidate = self._latest_creative_share_candidate()
        if not isinstance(candidate, dict):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        now = _now_ts()
        key = str(candidate.get("key") or "")
        completion_ratio = max(0.0, min(1.0, _safe_float(candidate.get("completion_ratio"), 0.0, 0.0)))
        maturity_score = _safe_float(candidate.get("maturity_score"), 0.0, 0.0)
        chunk_count = _safe_int(candidate.get("chunk_count"), 0, 0)
        disclosure_kind = _single_line(candidate.get("disclosure_kind"), 24) or "milestone"
        changed = False
        for user_id, user in users.items():
            if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                continue
            if not self._friend_can_receive_proactive_reason(user, "creative_share", "message"):
                continue
            idle_seconds = now - _safe_float(user.get("last_seen"), 0)
            required_idle = max(runtime_persona_setting(self, "idle_minutes", 60), 75) * 60
            if disclosure_kind == "ask_impression":
                required_idle = max(required_idle, 120 * 60)
            if idle_seconds < required_idle:
                continue
            if str(user.get("last_creative_share_key") or "") == key:
                continue
            if now - _safe_float(user.get("last_creative_share_at"), 0) < 8 * 3600:
                continue
            relation_score = _safe_int(user.get("relationship_score"), 0, -40, 120)
            ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0, 20)
            rel_bonus = min(0.18, max(0.0, relation_score / 220.0))
            pressure_penalty = min(0.2, ignored_streak * 0.04)
            mat_bonus = min(0.22, max(0.0, (maturity_score - 40.0) / 120.0))
            comp_bonus = min(0.12, completion_ratio * 0.18)
            imp_penalty = 0.12 if disclosure_kind == "ask_impression" else 0.0
            share_p = max(
                0.08,
                min(
                    0.88,
                    float(runtime_persona_setting(self, "creative_share_probability", 0.28) or 0.0)
                    + rel_bonus
                    + mat_bonus
                    + comp_bonus
                    - pressure_penalty
                    - imp_penalty,
                ),
            )
            if chunk_count < 2 and disclosure_kind != "finished":
                continue
            if random.random() > share_p:
                continue
            if disclosure_kind == "finished":
                delay_minutes = random.randint(15, 45)
            elif disclosure_kind == "ask_impression":
                delay_minutes = random.randint(35, 120)
            else:
                delay_minutes = random.randint(22, 90)
            scheduled = now + delay_minutes * 60
            title = _single_line(candidate.get("title"), 40) or "刚开的创作项目"
            work_type = _single_line(candidate.get("work_type"), 30) or "作品"
            accepted = self._offer_proactive_candidate(
                str(user_id), user,
                {
                    "source": "creative_writing", "reason": "creative_share",
                    "action": "message", "scheduled_ts": scheduled, "topic": title,
                    "score": int(max(68, min(90, 66 + maturity_score * 0.18 + relation_score * 0.05 - ignored_streak * 1.5))),
                    "motive": f"刚把{work_type}《{title}》推进到一个比较成形的小节点,想自然地给 {user_id} 看一句",
                    "context_key": "creative_share_context", "context": dict(candidate),
                },
            )
            if not accepted:
                continue
            user["last_creative_share_key"] = key
            user["last_creative_share_at"] = now
            self._mark_creative_milestone_disclosed(candidate)
            changed = True
        return changed

    def _creative_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None
