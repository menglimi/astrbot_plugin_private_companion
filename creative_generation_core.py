# -*- coding: utf-8 -*-
"""CreativeGenerationCoreMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 330 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations

from .creative_shared import _persona_provider_id, _render_creative_labeled_section, _render_creative_prompt
from .creative_shared import Any
from .creative_shared import _now_ts
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import logger
from .creative_shared import prompt_section
from .creative_shared import random
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_operation
from .creative_shared import uuid



class CreativeGenerationCoreMixin:
    """CreativeGenerationCoreMixin（从 CreativeMixin 拆出）。"""


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
                logger.debug("创作立项 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        persona_block = _render_creative_labeled_section(
            prompt_section(
                key="background.creative.project.persona",
                title="人格与身份",
                source="creative",
                content=persona_context,
            )
        )
        memory_block = _render_creative_labeled_section(
            prompt_section(
                key="background.creative.project.memory",
                title="我会牢牢记住你 创作连续性参考",
                source="creative",
                content=(
                    f"{memory_context or '暂无外部长期创作记忆。'}\n"
                    "使用方式：优先尊重用户长期偏好、已有项目、人工修订和避雷；不要说自己“查了记忆”。"
                ),
            )
        )
        direction_block = _render_creative_labeled_section(
            prompt_section(
                key="background.creative.project.direction",
                title="用户配置的创作方向",
                source="creative",
                content=direction_prompt or "未指定，按人格、灵感和连续性自然决定。",
            )
        )
        prompt = prompt_section(
            key="background.creative.project",
            title="私人创作立项",
            source="creative",
            content=f"""
 你是一个拟人化 Bot 的私人创作状态生成器。这个 Bot/角色会因为一个生活小事、日记碎片或梦境灵感,突然想开一个自己的创作项目。

{persona_block}

{memory_block}

{direction_block}

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
""".strip(),
        )
        text = await self._llm_call(
            _render_creative_prompt(prompt),
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

    @story_legacy_operation("creative.outline.generate")
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
                logger.debug("创作大纲 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = prompt_section(
            key="background.creative.outline",
            title="私人创作大纲",
            source="creative",
            content=f"""
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
""".strip(),
        )
        text = await self._llm_call(
            _render_creative_prompt(prompt), max_tokens=200,
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
                logger.debug("创作审稿 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = prompt_section(
            key="background.creative.review",
            title="私人创作审稿",
            source="creative",
            content=f"""
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
""".strip(),
        )
        text = await self._llm_call(
            _render_creative_prompt(prompt), max_tokens=220,
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
