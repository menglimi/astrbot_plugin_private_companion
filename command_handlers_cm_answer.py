# -*- coding: utf-8 -*-
"""手册本地与模型回答域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（8 个方法 + 0 个模块级名字 + 1 个类级赋值 / 473 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import asyncio
import re
from .command_handlers_shared import logger
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from pathlib import Path
from typing import Any



class CommandHandlersCmAnswerMixin:
    """手册本地与模型回答域（从 CommandHandlersMixin 拆出）。"""


    def _companion_manual_context_text(self, selected: list[dict[str, Any]] | None = None) -> str:
        entries = self._companion_manual_entries()
        selected_titles = {
            _single_line(item.get("title"), 80)
            for item in (selected or [])
            if isinstance(item, dict) and _single_line(item.get("title"), 80)
        }
        detailed_entries = [item for item in (selected or []) if isinstance(item, dict)][:4]
        blocks: list[str] = []
        for index, entry in enumerate(detailed_entries):
            title = _single_line(entry.get("title"), 80)
            if not title:
                continue
            checks = "；".join(str(item) for item in entry.get("checks", [])[:6] if str(item or "").strip())
            suggestions = "；".join(str(item) for item in entry.get("suggestions", [])[:4] if str(item or "").strip())
            settings = "；".join(
                self._companion_manual_config_ref(str(item), include_location=True)
                for item in entry.get("settings", [])[:12]
                if str(item or "").strip()
            )
            blocks.append(
                render_prompt_sections(
                    [
                        prompt_section(
                            key=f"background.manual_diagnosis.capability.{index}",
                            title=title,
                            source="command_handlers",
                            content="\n".join(
                                (
                                    f"逻辑：{entry.get('summary') or ''}",
                                    f"检查：{checks or '无'}",
                                    f"建议：{suggestions or '无'}",
                                    f"配置键：{settings or '无'}",
                                )
                            ),
                        )
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
            )
        index_lines: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = _single_line(entry.get("title"), 80)
            if not title or title in selected_titles:
                continue
            summary = _single_line(entry.get("summary"), 220)
            settings = ", ".join(
                _single_line(item, 80)
                for item in (entry.get("settings") if isinstance(entry.get("settings"), list) else [])[:8]
                if _single_line(item, 80)
            )
            index_lines.append(f"- {title}：{summary or '见插件实现'}；相关配置：{settings or '无'}")
        if index_lines:
            blocks.append(
                render_prompt_sections(
                    [
                        prompt_section(
                            key="background.manual_diagnosis.capability_index",
                            title="其他能力索引（用于发现相关链路，不是预设答案）",
                            source="command_handlers",
                            content="\n".join(index_lines),
                        )
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
            )
        return "\n\n".join(blocks)[:18000]

    @staticmethod
    def _companion_manual_source_file_names() -> tuple[str, ...]:
        return (
            "README.md",
            "CHANGELOG.md",
            "_conf_schema.json",
            "constants.py",
            "main.py",
            "command_handlers.py",
            "proactive.py",
            "proactive_engine.py",
            "proactive_message.py",
            "daily_state.py",
            "event_dispatch.py",
            "llm_tool_actions.py",
            "creative.py",
            "page_api.py",
            "user_memory.py",
            "private_image.py",
            "group_wakeup.py",
            "group_observation.py",
            "qzone_integration.py",
            "tts_enhancement.py",
            "token_budget.py",
        )

    # 巨型宿主按域拆分出的模块（<宿主>_*.py）。历史上主机名是**硬编码清单**，
    # 拆分后新增的域模块不在清单里 → 专家答疑检索不到对应实现，问题会被误判为
    # 「源码里没有这个功能」。这里按前缀动态展开，避免每次拆域都要回来补名字。
    _COMPANION_MANUAL_DERIVED_MODULE_PREFIXES: tuple[str, ...] = (
        "proactive_message_",
        "daily_state_",
        "proactive_engine_",
    )

    @classmethod
    def _companion_manual_resolve_source_files(cls) -> tuple[str, ...]:
        names: list[str] = list(cls._companion_manual_source_file_names())
        seen = set(names)
        root = Path(__file__).resolve().parent
        for prefix in cls._COMPANION_MANUAL_DERIVED_MODULE_PREFIXES:
            for path in sorted(root.glob(f"{prefix}*.py")):
                name = path.name
                if name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return tuple(names)

    def _companion_manual_source_context(
        self,
        question: str,
        selected: list[dict[str, Any]] | None = None,
        *,
        max_chars: int = 12000,
    ) -> str:
        """Retrieve small UTF-8 source excerpts so the model can answer beyond hard-coded FAQ entries."""
        query = str(question or "").strip()
        terms: list[str] = []

        def add_term(value: Any) -> None:
            term = _single_line(value, 100).strip().lower()
            if len(term) >= 2 and term not in terms:
                terms.append(term)

        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,12}", query):
            add_term(token)
        for entry in (selected or [])[:4]:
            if not isinstance(entry, dict):
                continue
            add_term(entry.get("title"))
            for keyword in (entry.get("keywords") if isinstance(entry.get("keywords"), list) else [])[:16]:
                keyword_text = _single_line(keyword, 40)
                if keyword_text and (keyword_text.lower() in query.lower() or len(keyword_text) <= 5):
                    add_term(keyword_text)
            for key in (entry.get("settings") if isinstance(entry.get("settings"), list) else [])[:12]:
                add_term(key)
        for key in self._companion_manual_mentioned_config_keys(query):
            add_term(key)
        if not terms:
            return ""

        root = Path(__file__).resolve().parent
        candidates: list[tuple[int, str, int, list[str]]] = []
        for file_name in self._companion_manual_resolve_source_files():
            path = root / file_name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            scored_lines: list[tuple[int, int]] = []
            for index, line in enumerate(lines):
                lowered = line.lower()
                score = sum((8 if "_" in term else min(5, len(term))) for term in terms if term in lowered)
                if score > 0:
                    scored_lines.append((score, index))
            scored_lines.sort(key=lambda item: (-item[0], item[1]))
            used_ranges: list[tuple[int, int]] = []
            for score, index in scored_lines[:12]:
                start = max(0, index - 3)
                end = min(len(lines), index + 5)
                if any(not (end <= old_start or start >= old_end) for old_start, old_end in used_ranges):
                    continue
                used_ranges.append((start, end))
                excerpt = lines[start:end]
                candidates.append((score, file_name, start + 1, excerpt))
                if len(used_ranges) >= 3:
                    break
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        blocks: list[str] = []
        length = 0
        for index, (_score, file_name, start_line, excerpt) in enumerate(candidates[:18]):
            block = render_prompt_sections(
                [
                    prompt_section(
                        key=f"background.manual_diagnosis.source_excerpt.{index}",
                        title=f"{file_name}:{start_line}",
                        source="command_handlers",
                        content="\n".join(excerpt),
                    )
                ],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if length + len(block) > max_chars:
                remaining = max_chars - length
                if remaining > 240:
                    blocks.append(block[:remaining])
                break
            blocks.append(block)
            length += len(block) + 2
        return "\n\n".join(blocks)

    def _companion_manual_local_answer(self, event: AstrMessageEvent, question: str) -> tuple[str, list[dict[str, Any]]]:
        query = _single_line(question, 260)
        if not query:
            return (
                "你直接说刚才发生了什么就行，比如：\n"
                "- 刚才群里为什么没回我\n"
                "- 发图后为什么等了几秒\n"
                "- 主动消息为什么今天没发\n"
                "- 这个开关开了到底有没有生效\n"
                "最好带上发生的场景、时间和一张截图；我会先查最可能的一条链路。"
            ), []
        selected = self._companion_manual_select_entries(query)
        if not selected:
            return (
                "我还没法把它定位到一条具体链路。\n"
                "把现象补成“在哪里发生 + Bot 做了什么/没做什么 + 大概什么时候”，我就能按运行态查。\n"
                "例如：群里 @ 了 Bot 但没回、私聊发图后等了 5 秒、刚才主动消息没发出来。"
            ), []
        primary = selected[0] if isinstance(selected[0], dict) else {}
        title = _single_line(primary.get("title"), 60) or "这条功能链路"
        summary = _single_line(primary.get("summary"), 180)
        issue_tags = self._companion_manual_issue_tags(query)
        recent_words = ("刚才", "刚刚", "今天", "最近", "没回", "不回", "没回复", "为什么", "失败", "报错")
        recent_requested = bool(issue_tags & {"recent", "error", "photo", "qzone"}) or any(word in query for word in recent_words)
        evidence: list[str] = []
        if recent_requested:
            evidence.extend(self._companion_manual_recent_no_reply_evidence(event, limit=1))
        if issue_tags & {"photo", "qzone"} or any(word in query for word in ("测试", "排障", "报错", "失败")):
            evidence.extend(self._companion_manual_recent_test_evidence(limit=1))
        runtime_lines = [
            _single_line(line, 130)
            for line in self._companion_manual_runtime_snapshot(event).splitlines()
            if _single_line(line, 130)
        ]
        if runtime_lines and ("group" in issue_tags or recent_requested):
            evidence.append(runtime_lines[0])
        checks = [_single_line(item, 120) for item in primary.get("checks", []) if _single_line(item, 120)]
        suggestions = [_single_line(item, 120) for item in primary.get("suggestions", []) if _single_line(item, 120)]
        mentioned_keys = self._companion_manual_mentioned_config_keys(query)
        for key in self._companion_manual_config_keys_from_alias_text(query, limit=2):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        lines = [f"这次更像是“{title}”在起作用。{summary}"]
        if evidence:
            lines.append("我现在能对上的证据是：" + "；".join(evidence[:2]))
        elif recent_requested:
            lines.append("当前没有直接命中这次事件的记录，所以只能先按现象判断，别把它当成确定结论。")
        if checks:
            lines.append("先看这一处：" + checks[0])
        elif suggestions:
            lines.append("下一步先试：" + suggestions[0])
        if mentioned_keys:
            key = mentioned_keys[0]
            current = self._companion_manual_current_config_value(key)
            lines.append(f"你点名的 {self._companion_manual_config_ref(key, include_location=False)}现在是 {self._companion_manual_format_config_item_value(key, current)}。")
        elif len(selected) > 1:
            secondary = _single_line(selected[1].get("title"), 50) if isinstance(selected[1], dict) else ""
            if secondary:
                lines.append(f"如果上面不符合，再查“{secondary}”，不用一次把所有开关都翻出来。")
        return "\n".join(line for line in lines if line), selected

    def _companion_manual_local_hint_text(self, event: AstrMessageEvent, selected: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        group_note = self._companion_manual_current_group_note(event)
        if group_note:
            lines.append(group_note)
        if selected:
            primary = selected[0] if isinstance(selected[0], dict) else {}
            title = _single_line(primary.get("title"), 60)
            if title:
                lines.append("优先链路：" + title)
            checks = [
                _single_line(item, 120)
                for item in (primary.get("checks") if isinstance(primary.get("checks"), list) else [])[:2]
                if _single_line(item, 120)
            ]
            if checks:
                lines.append("可验证点：" + "；".join(checks))
        runtime = [_single_line(item, 140) for item in self._companion_manual_runtime_snapshot(event).splitlines() if _single_line(item, 140)]
        if runtime:
            lines.append("运行态：" + "；".join(runtime[:2]))
        return "\n".join(line for line in lines if line)

    async def _companion_manual_model_answer(
        self,
        event: AstrMessageEvent,
        question: str,
        local_answer: str,
        selected: list[dict[str, Any]],
        media_context: str = "",
    ) -> str:
        caller = getattr(self, "_llm_call", None)
        if not callable(caller):
            return ""
        provider_selector = getattr(self, "_task_provider", None)
        if callable(provider_selector):
            provider_id = provider_selector(
                runtime_persona_setting(self, "troubleshooting_provider_id", ""),
                runtime_persona_setting(self, "complex_reasoning_provider_id", ""),
                runtime_persona_setting(self, "llm_provider_id", ""),
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            )
        else:
            provider_id = str(
                runtime_persona_setting(self, "troubleshooting_provider_id", "")
                or runtime_persona_setting(self, "complex_reasoning_provider_id", "")
                or runtime_persona_setting(self, "llm_provider_id", "")
                or runtime_persona_setting(self, "response_review_provider_id", "")
                or runtime_persona_setting(self, "mai_style_provider_id", "")
                or ""
            )
        if not provider_id:
            return ""
        manual_context = self._companion_manual_context_text(selected)
        local_hint = self._companion_manual_local_hint_text(event, selected) or self._companion_manual_clean_multiline(local_answer, limit=900)
        selected_hint = (
            "关键词初筛命中：" + " / ".join(_single_line(item.get("title"), 60) for item in selected if isinstance(item, dict))
            if selected
            else "关键词初筛未命中；请直接阅读完整说明书判断，不要把“未命中”当成答案。"
        )
        mentioned_keys = self._companion_manual_mentioned_config_keys(question)
        for key in self._companion_manual_config_keys_from_alias_text(question, limit=6):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        mentioned_config_text = (
            "\n".join(f"- {self._companion_manual_config_ref(key)}" for key in mentioned_keys)
            if mentioned_keys
            else "无"
        )
        recent_context = self._companion_manual_recent_context_text(event) or "没有同一会话内的上一轮答疑上下文。"
        persona_text = ""
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                await asyncio.wait_for(refresher(str(getattr(event, "unified_msg_origin", "") or "")), timeout=1.5)
            except Exception:
                pass
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                persona_text = _single_line(getter(), 700)
            except Exception:
                persona_text = ""
        runtime = self._companion_manual_runtime_snapshot(event)
        source_context = self._companion_manual_source_context(question, selected)
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="companion_manual_diagnosis",
                    query=(
                        f"陪伴插件答疑排障：{question}；"
                        "最近配置变动、失败日志、主动消息、群聊回复、自然语言生图、QQ空间、用户反馈、排障上下文"
                    ),
                    event=event,
                    top_k=5,
                    max_chars=900,
                    timeout_seconds=2.0,
                )
            except Exception as exc:
                logger.debug("答疑 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        instruction_section = prompt_section(
            key="background.manual_diagnosis.instructions",
            title="插件专家答疑任务",
            source="command_handlers",
            content=(
                "你是 PrivateCompanion 的插件专家答疑助手。你理解插件的功能边界、模块协作、配置、运行状态和关键实现，目标是像熟悉整个项目的维护者一样回答，而不是把问题套进关键词规则。\n\n"
                "回答方式：\n"
                "- 先直接回答用户真正问的内容。可以解释设计目的、实际调用链、模块关系、配置影响、已知限制、故障根因或改进方案，不要强行把所有问题改写成“现象排障”。\n"
                "- 以当前源码摘录、配置结构和真实运行状态为最高优先级；静态能力索引和关键词候选只帮助检索，绝不是预设结论。若候选与问题不符，必须忽略。\n"
                "- 用户问“能不能、为什么、怎么实现、这一改动是否有效”时，要结合实现链路进行推理，明确区分“部分解决”“完全解决”和“没有解决”。\n"
                "- 用户问最近发生的具体事件时才做现场诊断；有证据就引用关键证据，没有证据就明确不确定，不要用规则命中伪装成事实。\n"
                "- 回答深度由问题决定：简单问题可以短答，架构、代码或复杂排障可以充分展开，不受 3-6 行限制。\n"
                "- 不要把“完整功能说明书”“关键词初筛”“本地回退”“当前配置快照”“源码检索”等内部过程说给用户。\n"
                "- 不要编造不存在的配置项、模块、日志或已经执行过的操作；配置项以配置目录和源码为准。\n"
                "- 解释代码、公式、耗时或单位换算时，先逐项读取原表达式和数值单位，统一到基本单位计算，再换算展示单位并做反向换算复核；明确区分代码要求时长、实际运行耗时、日志值和界面显示值。\n"
                "- 不得引入源码、日志或用户材料中没有出现的运算、常数、倍率、对数或所谓解释器规则来凑结果，尤其不能凭空加入 ln、log、指数或除法。`time.sleep(10 * 60)` 就是 600 秒，即 10 分钟；若结果不符，应说明还缺哪段真实代码或日志。\n"
                "- 提到配置时必须同时写中文名和参数名,格式类似“高强度唤醒阈值（group_high_intensity_wakeup_threshold）”。\n"
                "- 涉及调参时，说明作用范围和副作用；只有证据支持时才给具体数值。\n"
                "- 可执行改配置由本地白名单规则另行生成；你只负责解释和建议,不要声称已经修改配置。\n"
                "- 语气自然、清楚，像插件作者本人在解释和排障，不要写客服套话。\n"
                "- 不要说“内置说明书没匹配到”“关键词没命中”“去扩展页排障中心”这类暴露实现的话；如果不确定,就自然说明需要更具体的现象或日志。\n"
                "- 不要要求用户复制文件；用户和你在同一机器上。"
            ),
        )
        context_sections = (
            prompt_section(key="background.manual_diagnosis.question", title="用户问题", source="command_handlers", content=_single_line(question, 260)),
            prompt_section(key="background.manual_diagnosis.persona", title="当前人格/说话风格参考", source="command_handlers", content=persona_text or "未读取到人格；保持简洁、自然、温和。"),
            prompt_section(key="background.manual_diagnosis.recent", title="同一会话上一轮答疑上下文", source="command_handlers", content=recent_context),
            prompt_section(key="background.manual_diagnosis.media", title="本轮图片/引用图片上下文", source="command_handlers", content=media_context or "本轮没有检测到随消息携带或引用的图片。"),
            prompt_section(
                key="background.manual_diagnosis.memory",
                title="我会牢牢记住你 最近排障/配置记忆",
                source="command_handlers",
                content=(
                    f"{memory_context or '暂无可用的近期记忆。'}\n"
                    "使用方式：只辅助理解这台实例最近发生过什么；本地运行状态、截图和日志证据优先。不要说“我查记忆发现”。"
                ),
            ),
            prompt_section(key="background.manual_diagnosis.candidates", title="关键词候选（只用于检索，可能不准确）", source="command_handlers", content=selected_hint),
            prompt_section(key="background.manual_diagnosis.manual", title="插件能力知识目录", source="command_handlers", content=manual_context),
            prompt_section(key="background.manual_diagnosis.sources", title="与本题相关的当前源码/文档摘录", source="command_handlers", content=source_context or "没有检索到直接相关的源码片段；此时只能依据能力目录、配置和运行状态回答。"),
            prompt_section(key="background.manual_diagnosis.config", title="用户明确提到的配置项", source="command_handlers", content=mentioned_config_text),
            prompt_section(key="background.manual_diagnosis.runtime", title="当前运行状态快照", source="command_handlers", content=runtime or "没有拿到当前会话专项状态,只能按配置和说明书判断。"),
            prompt_section(key="background.manual_diagnosis.evidence", title="本地采集到的候选证据（可能与问题无关，不得直接当结论）", source="command_handlers", content=local_hint),
        )
        output_section = prompt_section(
            key="background.manual_diagnosis.output",
            title="插件专家答疑输出要求",
            source="command_handlers",
            content="请输出：\n直接回答用户的问题。按问题复杂度组织内容，必要时说明调用链、依据、边界和下一步。",
        )
        prompt = "\n\n".join(
            (
                render_prompt_sections([instruction_section], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(context_sections, mode=PromptRenderMode.LABELED_BLOCK),
                render_prompt_sections([output_section], mode=PromptRenderMode.BODY_ONLY),
            )
        )
        timeout_resolver = getattr(self, "_model_timeout_seconds_for_call", None)
        answer_timeout = None
        if callable(timeout_resolver):
            try:
                answer_timeout = timeout_resolver(
                    task="companion_manual_diagnosis",
                    provider_id=provider_id,
                    timeout_key="TROUBLESHOOTING_PROVIDER_ID",
                )
            except Exception:
                answer_timeout = None
        answer_timeout = max(15.0, min(180.0, float(answer_timeout or 45.0)))
        try:
            raw = await asyncio.wait_for(
                caller(
                    prompt,
                    max_tokens=1400,
                    provider_id=provider_id,
                    task="companion_manual_diagnosis",
                    timeout_key="TROUBLESHOOTING_PROVIDER_ID",
                    timeout_seconds=answer_timeout,
                ),
                timeout=answer_timeout + 3.0,
            )
        except asyncio.TimeoutError:
            logger.warning("陪伴答疑模型诊断超时,回退本地说明: question=%s", _single_line(question, 120))
            return ""
        except Exception as exc:
            logger.warning("陪伴答疑模型诊断失败,回退本地说明: %s", _single_line(exc, 120))
            return ""
        return self._companion_manual_clean_multiline(raw, limit=1800)

    async def _companion_manual_answer(self, event: AstrMessageEvent, question: str) -> str:
        query = self._companion_manual_clean_question_text(question, 260)
        media_context = await self._companion_manual_media_context(event, query)
        if not query and media_context:
            query = "根据本轮携带或引用的图片做插件答疑/排障"
        local_answer, selected = self._companion_manual_local_answer(event, query)
        if not query:
            self._companion_manual_store_pending_config(event, query, [])
            return local_answer
        proposals = self._companion_manual_build_config_proposals(query, selected, event)
        token = self._companion_manual_store_pending_config(event, query, proposals)
        explicit_config_question = bool(self._companion_manual_mentioned_config_keys(query)) or any(
            word in query.lower()
            for word in ("配置", "设置", "参数", "阈值", "开关", "改成", "调到", "调高", "调低", "怎么开", "怎么关")
        )
        proposal_text = (
            self._companion_manual_format_config_proposals_brief(token, proposals)
            if explicit_config_question
            else ""
        )
        model_answer = await self._companion_manual_model_answer(event, query, local_answer, selected, media_context=media_context)
        if model_answer:
            answer = model_answer
        else:
            answer = self._companion_manual_fallback_answer(event, query, selected, proposals, media_context=media_context)
        if proposal_text:
            answer = f"{answer}\n\n{proposal_text}"
        self._companion_manual_store_recent_context(event, question=query, answer=answer, proposals=proposals)
        return answer
