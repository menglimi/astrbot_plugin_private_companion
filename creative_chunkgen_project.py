# -*- coding: utf-8 -*-
"""CreativeChunkgenProjectMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 225 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations

from .creative_shared import _persona_provider_id, _render_creative_labeled_section, _render_creative_prompt
from .creative_shared import Any
from .creative_shared import CREATIVE_REVIEW_MIN_SCORE
from .creative_shared import CREATIVE_SIMILARITY_RETRIES
from .creative_shared import _now_ts
from .creative_shared import _safe_float
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import logger
from .creative_shared import prompt_section
from .creative_shared import random
from .creative_shared import re
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_operation
from .creative_shared import story_legacy_sync_operation



class CreativeChunkgenProjectMixin:
    """CreativeChunkgenProjectMixin（从 CreativeMixin 拆出）。"""


    @story_legacy_operation("creative.chunk.generate")
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
                logger.debug("创作续写 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))

        async def _do_generate(extra_notice: str = "") -> str:
            story_time = _single_line(story_bible.get("story_time"), 60)
            persona_block = _render_creative_labeled_section(
                prompt_section(
                    key="background.creative.writing.persona",
                    title="作者人格与身份",
                    source="creative",
                    content=persona_context,
                )
            )
            prompt = prompt_section(
                key="background.creative.writing",
                title="私人创作正文",
                source="creative",
                content=f"""
你就是下面这个人格,此刻正在闲暇时写自己想写的作品。请只写本次随手能写下的一小段。

{persona_block}

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
""".strip(),
            )
            text = await self._llm_call(
                _render_creative_prompt(prompt), max_tokens=max(220, budget + 160),
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

    @story_legacy_sync_operation("creative.project.defer")
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

    @story_legacy_operation("creative.project.start")
    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        if not runtime_persona_setting(self, "enable_creative_writing", False):
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
        logger.info("新增创作项目: %s / %s", project.get("work_type"), project.get("title"))
        return True
