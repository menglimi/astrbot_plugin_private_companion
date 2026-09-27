# -*- coding: utf-8 -*-
"""模型诊断域。

由 tools/split_mixin_domain.py 从 page_api_diagnostics.py 机械抽取（7 个方法 + 1 个模块级名字 + 0 个类级赋值 / 519 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsMixin）。
"""
from __future__ import annotations

import re
import time
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from copy import deepcopy
from typing import Any



def _render_page_background_prompt(
    *,
    key: str,
    title: str,
    content: str,
) -> str:
    return render_prompt_sections(
        [
            prompt_section(
                key=key,
                title=title,
                source="page_api",
                content=content,
            )
        ],
        mode=PromptRenderMode.BODY_ONLY,
    )


class PrivateCompanionPageApiDiagnosticsModelMixin:
    """模型诊断域（从 PrivateCompanionPageApiDiagnosticsMixin 拆出）。"""


    def _troubleshooting_role_appearance_prompt(self) -> str:
        persona = str(getattr(self.plugin, "schedule_persona_prompt", "") or self._config_get("schedule_persona_prompt") or "")
        recognition = str(
            getattr(self.plugin, "private_image_self_recognition_hint", "")
            or self._config_get("private_image_self_recognition_hint")
            or ""
        )
        if not persona and not recognition:
            return ""
        label_prefix = {
            "姓名": "角色名",
            "种族": "种族",
            "性别": "性别",
            "识别点": "主要识别点",
            "外貌": "外貌",
            "主要识别点": "主要识别点",
            "发型发色": "发型发色",
            "发色": "发色",
            "发型": "发型",
            "瞳色": "瞳色",
            "眼睛": "眼睛",
            "服饰风格": "服饰风格",
            "服装": "服装",
            "衣着": "衣着",
        }
        visual_labels = {
            "识别点",
            "外貌",
            "主要识别点",
            "发型发色",
            "发色",
            "发型",
            "瞳色",
            "眼睛",
            "服饰风格",
            "服装",
            "衣着",
        }
        parts: list[str] = []
        has_visual = bool(recognition)
        for line in str(persona or "").replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = self._single_line(value, 140)
            if label in label_prefix and value:
                if label in visual_labels:
                    has_visual = True
                parts.append(f"{label_prefix[label]}：{value}")
        if recognition:
            parts.append(f"补充识别线索{self._single_line(recognition, 180)}")
        if not has_visual:
            return ""
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return self._single_line("，".join(unique), 420)

    async def _run_model_diagnostics_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        use_model = self._normalize_bool_value(payload.get("use_model", True))
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        skill_items = self._skill_similarity_local_candidates(data)
        slang_items = self._model_diagnostics_slang_candidates(data)
        pending_items = self._model_diagnostics_pending_observation_candidates(data)
        memory_items = self._model_diagnostics_companion_memory_candidates(data)
        expression_items = self._model_diagnostics_expression_candidates(data)
        total_local = len(skill_items) + len(slang_items) + len(pending_items) + len(memory_items) + len(expression_items)
        sections: list[dict[str, Any]] = [
            {
                "key": "skills",
                "title": "技能相似项",
                "local_count": len(skill_items),
                "model_count": 0,
                "suggestions": [
                    f"技能｜{item.get('a')} ↔ {item.get('b')}：{item.get('reason')}"
                    for item in skill_items[:4]
                ],
            },
            {
                "key": "slang",
                "title": "群黑话杂音",
                "local_count": len(slang_items),
                "model_count": 0,
                "suggestions": [
                    f"黑话｜{item.get('group_name') or item.get('group_id')}｜{item.get('term')}：{item.get('reason')}"
                    for item in slang_items[:4]
                ],
            },
            {
                "key": "worldbook",
                "title": "关系网待确认观察",
                "local_count": len(pending_items),
                "model_count": 0,
                "suggestions": [
                    f"关系网｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('evidence')}"
                    for item in pending_items[:4]
                ],
            },
            {
                "key": "memory",
                "title": "本地画像噪音",
                "local_count": len(memory_items),
                "model_count": 0,
                "suggestions": [
                    f"本地画像｜{item.get('name') or item.get('user_id')}｜{item.get('field')}：{item.get('reason')}｜{item.get('text')}"
                    for item in memory_items[:4]
                ],
            },
            {
                "key": "expression",
                "title": "表达规则重复与污染",
                "local_count": len(expression_items),
                "model_count": 0,
                "suggestions": [
                    f"表达学习｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('text')}"
                    for item in expression_items[:4]
                ],
            },
        ]
        steps: list[dict[str, str]] = [
            {
                "name": "本地规则",
                "status": "ok" if total_local else "info",
                "detail": (
                    (
                        f"发现 {total_local} 条候选：技能 {len(skill_items)}、黑话 {len(slang_items)}、"
                        f"关系网 {len(pending_items)}、本地画像 {len(memory_items)}、表达学习 {len(expression_items)}"
                    )
                    if total_local
                    else "未发现明显技能冲突、黑话杂音、无效关系观察或私聊学习污染"
                ),
            }
        ]
        model_items: list[str] = []
        provider_id = ""
        if use_model and total_local:
            caller = getattr(self.plugin, "_llm_call", None)
            if callable(caller):
                provider_selector = getattr(self.plugin, "_task_provider", None)
                if callable(provider_selector):
                    provider_id = provider_selector(
                        getattr(self.plugin, "troubleshooting_provider_id", ""),
                        getattr(self.plugin, "response_review_provider_id", ""),
                        getattr(self.plugin, "mai_style_provider_id", ""),
                        getattr(self.plugin, "llm_provider_id", ""),
                    )
                else:
                    provider_id = str(
                        getattr(self.plugin, "troubleshooting_provider_id", "")
                        or getattr(self.plugin, "response_review_provider_id", "")
                        or getattr(self.plugin, "mai_style_provider_id", "")
                        or getattr(self.plugin, "llm_provider_id", "")
                        or ""
                    )
                prompt = self._model_diagnostics_review_prompt(
                    data,
                    skill_items,
                    slang_items,
                    pending_items,
                    memory_items,
                    expression_items,
                )
                try:
                    raw = await caller(
                        prompt,
                        max_tokens=700,
                        provider_id=provider_id,
                        task="troubleshooting_model_diagnostics",
                    )
                    model_items = self._parse_skill_similarity_model_result(raw)
                    self._attach_model_diagnostics_section_suggestions(sections, model_items)
                    steps.append(
                        {
                            "name": "模型复核",
                            "status": "ok" if model_items else "info",
                            "detail": f"模型给出 {len(model_items)} 条建议" if model_items else "模型未给出额外排障建议",
                        }
                    )
                except Exception as exc:
                    steps.append({"name": "模型复核", "status": "warn", "detail": f"调用失败: {self._single_line(exc, 120)}"})
            else:
                steps.append({"name": "模型复核", "status": "warn", "detail": "插件缺少 _llm_call，无法调用模型"})
        elif not use_model:
            steps.append({"name": "模型复核", "status": "info", "detail": "本次按请求仅执行本地规则检查"})
        elif not total_local:
            steps.append({"name": "模型复核", "status": "info", "detail": "没有本地候选，未调用模型"})

        local_preview = []
        for section in sections:
            local_preview.extend(section.get("suggestions") if isinstance(section.get("suggestions"), list) else [])
        all_suggestions = [*local_preview]
        for item in model_items:
            if item not in all_suggestions:
                all_suggestions.append(item)
        detail = (
            f"本地发现 {total_local} 条候选，模型给出 {len(model_items)} 条建议"
            if all_suggestions
            else "模型相关数据目前没有明显杂音"
        )
        return {
            "ok": True,
            "title": "模型数据排障",
            "provider": self._single_line(provider_id, 100),
            "detail": self._single_line(detail, 220),
            "text_preview": self._single_line(" | ".join(all_suggestions[:5]), 220),
            "suggestions": [self._single_line(item, 220) for item in all_suggestions[:12]],
            "sections": sections,
            "local_count": total_local,
            "model_count": len(model_items),
            "suggestion_count": len(all_suggestions),
            "extra_count": max(0, len(all_suggestions) - 5),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "",
        }

    def _model_diagnostics_slang_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        common_terms = {
            "什么",
            "这个",
            "那个",
            "就是",
            "可以",
            "没有",
            "真的",
            "一下",
            "今天",
            "明天",
            "昨天",
            "然后",
            "现在",
            "等等",
            "不是",
            "因为",
            "所以",
            "但是",
            "感觉",
            "可能",
            "应该",
            "好像",
            "知道",
            "看看",
            "消息",
            "图片",
        }
        reaction_terms = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊", "救命", "失败", "报错"}
        media_markers = ("[图片]", "[视频]", "[语音]", "[表情]", "[文件]", "[CQ:", "http://", "https://")
        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_name = self._single_line(group.get("name") or group.get("group_name") or group_id, 40)
            terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
            meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
            indexed: list[dict[str, Any]] = []
            for raw in terms:
                if isinstance(raw, dict):
                    indexed.append(raw)
                else:
                    indexed.append({"term": raw})
            for raw in indexed[:160]:
                term = self._single_line(raw.get("term") if isinstance(raw, dict) else raw, 50)
                if not term:
                    continue
                if isinstance(raw, dict) and self._single_line(raw.get("source"), 20) == "manual":
                    continue
                compact = re.sub(r"\s+", "", term)
                reason = ""
                if any(marker.lower() in term.lower() for marker in media_markers):
                    reason = "像媒体占位或链接，不像黑话"
                elif re.fullmatch(r"\d{5,}", compact):
                    reason = "像纯数字 ID，不像黑话"
                elif compact in common_terms:
                    reason = "像普通高频词，不像群内黑话"
                elif compact in reaction_terms:
                    reason = "像一次性语气/反应词，容易污染黑话"
                elif len(compact) <= 1 and not re.fullmatch(r"[a-zA-Z]+", compact):
                    reason = "过短，缺少可解释语义"
                elif len(compact) >= 18 and re.search(r"[。！？!?，,]", term):
                    reason = "像整句聊天内容，不像词条"
                else:
                    raw_meaning = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
                    meaning = self._single_line(raw_meaning.get("meaning"), 80) if isinstance(raw_meaning, dict) else ""
                    confidence = self._float(raw_meaning.get("confidence")) if isinstance(raw_meaning, dict) else 0.0
                    count = self._int(raw.get("count")) if isinstance(raw, dict) else 0
                    if meaning and confidence < 0.35 and count <= 2:
                        reason = "低频且释义置信度很低"
                    elif meaning and any(marker in meaning for marker in ("无法判断", "语境不明", "不确定")) and count <= 2:
                        reason = "释义长期不确定，建议复核"
                if not reason:
                    continue
                key = (str(group_id), term)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "group_id": self._single_line(group_id, 40),
                        "group_name": group_name,
                        "term": term,
                        "reason": reason,
                        "count": str(self._int(raw.get("count")) if isinstance(raw, dict) else 0),
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates

    def _model_diagnostics_pending_observation_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        generic_reactions = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊啊", "救命", "离谱", "不是吧"}
        log_markers = ("Traceback", "[ERROR]", "[WARN]", "Exception", "File \"", "```", "ERROR", "WARN")
        candidates: list[dict[str, str]] = []
        seen_global: set[str] = set()
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            name = self._single_line(profile.get("name") or user_id, 40)
            pending = profile.get("pending_observations") if isinstance(profile.get("pending_observations"), list) else []
            seen_local: set[str] = set()
            for raw in pending[:12]:
                if not isinstance(raw, dict):
                    continue
                evidence = self._single_line(raw.get("evidence") or raw.get("content") or raw.get("title"), 140)
                content = self._single_line(raw.get("content") or evidence, 180)
                compact = re.sub(r"[\s，。！？!?~～…、,.]+", "", evidence)
                norm = compact.lower()
                reason = ""
                if not compact or len(compact) <= 2:
                    reason = "内容过短，难以沉淀成人物观察"
                elif compact in generic_reactions:
                    reason = "像临时语气反应，不像稳定人物信息"
                elif any(marker in evidence or marker in content for marker in log_markers):
                    reason = "像日志/代码片段，不适合写入关系网"
                elif norm in seen_local:
                    reason = "同一成员下重复观察"
                elif norm in seen_global:
                    reason = "跨成员重复泛化观察，可能没有辨识度"
                elif re.fullmatch(r"[\dA-Za-z_-]{8,}", compact):
                    reason = "像 ID 或文件名，不像人物观察"
                elif any(marker in evidence for marker in ("今天", "刚刚", "现在", "一会儿", "等下")) and self._int(raw.get("count")) <= 1:
                    reason = "像临时状态，缺少长期价值"
                seen_local.add(norm)
                seen_global.add(norm)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "title": self._single_line(raw.get("title"), 50),
                        "evidence": evidence,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates

    def _model_diagnostics_companion_memory_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        temporary_tokens = ("今天", "刚刚", "刚才", "现在", "今晚", "明天", "这次", "暂时", "一会儿", "等会儿", "刚睡醒", "刚下课")
        joke_tokens = ("开玩笑", "不是认真的", "随口", "口嗨", "逗你的", "反话", "阴阳怪气")
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "git ", "python ", "node ")
        internal_markers = ("提示词", "系统提示", "内部控制标签", "插件配置", "schema", "token", "cache hit", "PCTTS", "<pc_tts")
        profile_fields = {
            "strong_memories": "强记忆",
            "weak_preferences": "弱偏好",
            "user_traits": "用户画像",
            "interests": "兴趣/偏好",
            "boundaries": "边界/雷点",
            "relationship_notes": "关系线索",
            "speaking_style": "说话习惯",
        }

        def reason_for(text: str) -> str:
            if any(marker.lower() in text.lower() for marker in log_markers):
                return "像日志/代码/报错内容，不适合本地画像"
            if any(marker.lower() in text.lower() for marker in internal_markers):
                return "像系统或插件内部文本，不适合本地画像"
            if any(token in text for token in joke_tokens):
                return "带有玩笑/反讽不确定性，建议人工确认"
            if any(token in text for token in temporary_tokens) and not any(token in text for token in ("以后", "长期", "一直", "固定", "默认", "记住", "记得")):
                return "像临时状态，不像本地画像"
            if re.fullmatch(r"[\dA-Za-z_-]{8,}", re.sub(r"\s+", "", text)):
                return "像 ID 或文件名，不像用户画像"
            return ""

        candidates: list[dict[str, str]] = []
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            name = self._single_line(user.get("nickname") or user.get("name") or user_id, 40)
            memory = user.get("companion_memory") if isinstance(user.get("companion_memory"), dict) else {}
            raw_items = memory.get("items") if isinstance(memory.get("items"), list) else []
            for raw in raw_items[:24]:
                if not isinstance(raw, dict):
                    continue
                text = self._single_line(raw.get("text"), 160)
                reason = reason_for(text)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "field": self._single_line(raw.get("kind") or "原始记录", 30),
                        "text": text,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
            profile = memory.get("profile") if isinstance(memory.get("profile"), dict) else {}
            for key, label in profile_fields.items():
                value = profile.get(key)
                values = value if isinstance(value, list) else ([value] if value else [])
                for raw_text in values[:8]:
                    text = self._single_line(raw_text, 160)
                    reason = reason_for(text)
                    if not reason:
                        continue
                    candidates.append(
                        {
                            "user_id": self._single_line(user_id, 40),
                            "name": name,
                            "field": label,
                            "text": text,
                            "reason": reason,
                        }
                    )
                    if len(candidates) >= 24:
                        return candidates
        return candidates

    def _model_diagnostics_review_prompt(
        self,
        data: dict[str, Any],
        skill_candidates: list[dict[str, str]],
        slang_candidates: list[dict[str, str]],
        pending_candidates: list[dict[str, str]],
        memory_candidates: list[dict[str, str]],
        expression_candidates: list[dict[str, str]],
    ) -> str:
        skill_lines = [f"- {item.get('a')} / {item.get('b')}：{item.get('reason')}" for item in skill_candidates[:12]]
        slang_lines = [
            f"- {item.get('group_name') or item.get('group_id')}｜{item.get('term')}｜{item.get('reason')}｜次数:{item.get('count') or 0}"
            for item in slang_candidates[:18]
        ]
        pending_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('evidence')}"
            for item in pending_candidates[:18]
        ]
        memory_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('field')}｜{item.get('reason')}｜{item.get('text')}"
            for item in memory_candidates[:18]
        ]
        expression_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('text')}"
            for item in expression_candidates[:18]
        ]
        return _render_page_background_prompt(
            key="background.troubleshooting.model_diagnostics",
            title="模型数据排障复核",
            content=(
                "你是陪伴插件的数据排障助手。请复核下面这些由本地规则挑出的候选项，只指出明显会影响模型理解的杂音。\n"
            "范围包括：技能相似项、群黑话杂音、关系网待确认观察、本地画像噪音、表达规则重复与污染。不要修改数据，不要发散，不要把正常口癖或真实群梗误报。\n"
            "表达学习候选由本地规则预筛：污染项关注日志、复制模型格式、政治敏感内容和过度标点；重复项关注同一来源内已启用/待审核规则的同模板、同证据或高相似变体，以及运行时规则预算占用。\n"
            "不要因为两条规则语气相似就建议删除；模板虽然相同但意图、关系阶段或情绪边界不兼容时应保留。auto_merge=false 的近似项只建议人工复核，不得声称已经合并。\n"
            "输出 1-10 条短建议，每条不超过 45 字，必须用分类前缀：技能｜、黑话｜、关系网｜、本地画像｜、表达学习｜。\n"
            "如果某一类没有明显问题，不要为了凑数输出。若全部无明显问题，输出“未发现明显模型数据杂音”。\n\n"
            "技能候选：\n" + ("\n".join(skill_lines) if skill_lines else "- 无") + "\n\n"
            "群黑话候选：\n" + ("\n".join(slang_lines) if slang_lines else "- 无") + "\n\n"
            "关系网待确认观察候选：\n" + ("\n".join(pending_lines) if pending_lines else "- 无") + "\n\n"
            "本地画像候选：\n" + ("\n".join(memory_lines) if memory_lines else "- 无") + "\n\n"
                "表达学习候选：\n" + ("\n".join(expression_lines) if expression_lines else "- 无")
            ),
        )

    def _attach_model_diagnostics_section_suggestions(self, sections: list[dict[str, Any]], suggestions: list[str]) -> None:
        section_by_key = {str(section.get("key")): section for section in sections if isinstance(section, dict)}
        prefix_map = {
            "技能": "skills",
            "黑话": "slang",
            "关系网": "worldbook",
            "本地画像": "memory",
            "表达学习": "expression",
        }
        for suggestion in suggestions:
            prefix = self._single_line(str(suggestion).split("｜", 1)[0], 20)
            key = prefix_map.get(prefix)
            section = section_by_key.get(key or "")
            if not section:
                continue
            items = section.setdefault("suggestions", [])
            if isinstance(items, list):
                items.append(self._single_line(suggestion, 180))
            section["model_count"] = self._int(section.get("model_count")) + 1
