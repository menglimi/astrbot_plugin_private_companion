# -*- coding: utf-8 -*-
"""CreativePersonaMemoryContextMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 291 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations
from .creative_shared import Any
from .creative_shared import CREATIVE_MEMORY_MAX_ENTRIES
from .creative_shared import CREATIVE_SIMILARITY_THRESHOLD
from .creative_shared import CREATIVE_STORY_BIBLE_TEMPLATE
from .creative_shared import _now_ts
from .creative_shared import _safe_float
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import _text_similarity
from .creative_shared import deepcopy
from .creative_shared import random
from .creative_shared import re
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_context
from .creative_shared import story_legacy_sync_operation
from .creative_shared import uuid



class CreativePersonaMemoryContextMixin:
    """CreativePersonaMemoryContextMixin（从 CreativeMixin 拆出）。"""


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

    @story_legacy_sync_operation("creative.story-bible.ensure")
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

    @story_legacy_sync_operation("creative.memory-pool.ensure")
    def _get_or_create_memory_pool(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        pool = project.get("creative_memory_pool")
        if not isinstance(pool, list):
            pool = []
            project["creative_memory_pool"] = pool
        project_id = str(project.get("id") or "")
        valid: list[dict[str, Any]] = []
        for item in pool:
            if not isinstance(item, dict):
                continue
            owner = str(item.get("project_id") or "")
            if owner and owner != project_id:
                continue
            if not owner:
                item["project_id"] = project_id
            valid.append(item)
        valid.sort(
            key=lambda item: (
                _safe_int(item.get("importance"), 2, 1, 5),
                _safe_float(item.get("created_at"), 0.0),
            ),
            reverse=True,
        )
        pool[:] = valid[:CREATIVE_MEMORY_MAX_ENTRIES]
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

    @story_legacy_sync_operation("creative.memory-entry.add")
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
            with story_legacy_context("creative.characters.normalize"):
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
