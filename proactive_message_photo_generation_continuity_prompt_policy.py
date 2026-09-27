# -*- coding: utf-8 -*-
"""连续性记忆与提示词策略域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 615 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .conversation_prompt_section import PhotoPromptContent, PromptSection, prompt_section
from .helpers import (
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    normalize_bot_relationship_cards,
)
from .persona_config import runtime_persona_setting
from .proactive_message_photo_generation_shared import _now_ts, logger
from dataclasses import replace
from pathlib import Path
from typing import Any



class ProactiveMessagePhotoGenerationContinuityPromptPolicyMixin:
    """连续性记忆与提示词策略域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _photo_generation_result_metadata(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        raw = self.data.get("recent_photo_generations") if isinstance(getattr(self, "data", None), dict) else []
        if not isinstance(raw, list):
            return {}
        target_path = _path_text(image_path, 1000)
        target_session = _single_line(session_key, 340)
        for item in raw:
            if not isinstance(item, dict):
                continue
            if target_path and _path_text(item.get("path"), 1000) != target_path:
                continue
            if not target_path and target_session and _single_line(item.get("session"), 340) != target_session:
                continue
            return dict(item)
        return {}

    @staticmethod
    def _normalize_photo_continuity_key(value: Any) -> str:
        key = _single_line(value, 340).strip()
        if key.startswith("tool_photo_"):
            key = key[len("tool_photo_") :]
        return key

    @classmethod
    def _compose_photo_continuity_key(cls, session_key: Any, user_id: Any) -> str:
        session = cls._normalize_photo_continuity_key(session_key)
        sender = _single_line(user_id, 80).strip()
        if not session or not sender:
            return ""
        return _single_line(f"{session}|sender={sender}", 340)

    @classmethod
    def _photo_continuity_store_key(cls, continuity_key: Any) -> str:
        normalized = cls._normalize_photo_continuity_key(continuity_key)
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def _remember_sent_photo_continuity_reference(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not bool(item.get("ok")) or not bool(item.get("sent")):
            return
        continuity_key = self._normalize_photo_continuity_key(item.get("continuity_key"))
        store_key = self._photo_continuity_store_key(continuity_key)
        image_path = _path_text(item.get("path"), 1000)
        if not store_key or not image_path:
            return
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return
        except (OSError, ValueError):
            return

        final_presets = [
            _single_line(value, 80)
            for value in (item.get("presets") if isinstance(item.get("presets"), list) else [])
            if _single_line(value, 80)
        ]
        final_scene_preset = (
            final_presets[0]
            if final_presets
            else (
                _single_line(item.get("scene_preset"), 80)
                if _safe_int(item.get("schema_version"), 1) >= 2
                else ""
            )
        )
        now = _now_ts()
        raw_store = self.data.setdefault("recent_photo_continuity", {})
        if not isinstance(raw_store, dict):
            raw_store = {}
            self.data["recent_photo_continuity"] = raw_store
        raw_store[store_key] = {
            "schema_version": 2,
            "continuity_key": continuity_key,
            "sent_at": now,
            "generated_at": _safe_float(item.get("ts"), now),
            "path": str(path),
            "kind": _single_line(item.get("kind"), 30),
            "intent_kind": _single_line(item.get("intent_kind"), 30),
            "prompt": _single_line(item.get("prompt"), 900),
            "caption": _single_line(item.get("caption"), 160),
            "scene_preset": final_scene_preset,
            "preset_source": _single_line(item.get("preset_source"), 40),
            "reference_path": _path_text(item.get("reference_path"), 1000),
            "wardrobe_mode": _single_line(item.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(item.get("wardrobe_category"), 40),
            "reference_roles": list(item.get("reference_roles") or []),
        }

        keep_after = now - 24 * 3600
        for key, record in list(raw_store.items()):
            if not isinstance(record, dict) or _safe_float(record.get("sent_at"), 0) < keep_after:
                raw_store.pop(key, None)
        if len(raw_store) > 96:
            ordered = sorted(
                raw_store.items(),
                key=lambda pair: _safe_float(pair[1].get("sent_at"), 0) if isinstance(pair[1], dict) else 0,
                reverse=True,
            )
            self.data["recent_photo_continuity"] = dict(ordered[:96])

    def _recent_sent_photo_continuity_candidate(
        self,
        continuity_key: Any,
        *,
        now: float | None = None,
        max_age_seconds: float = 45 * 60,
    ) -> dict[str, str]:
        normalized = self._normalize_photo_continuity_key(continuity_key)
        store_key = self._photo_continuity_store_key(normalized)
        data = getattr(self, "data", {})
        raw_store = data.get("recent_photo_continuity") if isinstance(data, dict) else {}
        record = raw_store.get(store_key) if store_key and isinstance(raw_store, dict) else None
        if not isinstance(record, dict):
            return {}
        if self._normalize_photo_continuity_key(record.get("continuity_key")) != normalized:
            return {}
        check_now = _now_ts() if now is None else float(now)
        sent_at = _safe_float(record.get("sent_at"), 0)
        age = check_now - sent_at
        if sent_at <= 0 or age < -300 or age > max(60.0, float(max_age_seconds)):
            return {}
        image_path = _path_text(record.get("path"), 1000)
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return {}
        except (OSError, ValueError):
            return {}
        previous_prompt = _single_line(record.get("prompt"), 360)
        previous_caption = _single_line(record.get("caption"), 120)
        record_schema_version = _safe_int(record.get("schema_version"), 1)
        previous_scene = (
            _single_line(record.get("scene_preset"), 80)
            if record_schema_version >= 2
            else ""
        )
        details = "；".join(
            part
            for part in (
                f"上一张画面要求：{previous_prompt}" if previous_prompt else "",
                f"上一张附言：{previous_caption}" if previous_caption else "",
                f"上一张场景预设：{previous_scene}" if previous_scene else "",
            )
            if part
        )
        note = (
            "同一会话刚刚已经实际发送的上一张成图；只有当前要求是在原画面上自然续拍，主要改变动作、表情、视线、机位或近似构图时使用；"
            "若明确更换人物、服装、地点、时间、整体场景或另起主题则不要使用"
        )
        if details:
            note = f"{note}；{details}"
        return {
            "id": "recent_sent_photo",
            "path": str(path),
            "source": str(path),
            "kind": "recent_sent_photo",
            "note": _single_line(note, 760),
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_category": _single_line(record.get("wardrobe_category"), 40),
            "outfit_lock_default": True,
            "preferred_preset": previous_scene,
            "metadata_source": "runtime",
        }

    def _annotate_recent_photo_generation(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
        trigger: str = "",
        intent_kind: str = "",
        sent: bool | None = None,
        caption: str = "",
        preset_hint: str = "",
        tool_name: str = "",
    ) -> None:
        try:
            raw = self.data.get("recent_photo_generations")
            if not isinstance(raw, list):
                return
            target_path = _path_text(image_path, 1000)
            target_session = _single_line(session_key, 340)
            for item in raw:
                if not isinstance(item, dict):
                    continue
                same_path = bool(target_path and _path_text(item.get("path"), 1000) == target_path)
                same_session = bool(target_session and _single_line(item.get("session"), 340) == target_session)
                if not (same_path if target_path else same_session):
                    continue
                if trigger:
                    item["trigger"] = _single_line(trigger, 40)
                if intent_kind:
                    item["intent_kind"] = _single_line(intent_kind, 30)
                if sent is not None:
                    item["sent"] = bool(sent)
                if caption:
                    item["caption"] = _single_line(caption, 120)
                if preset_hint:
                    item["preset_hint"] = _single_line(preset_hint, 80)
                if tool_name:
                    item["tool_name"] = _single_line(tool_name, 60)
                item["annotated_at"] = _now_ts()
                if sent is True:
                    self._remember_sent_photo_continuity_reference(item)
                save_sections = {"recent_photo_generations"}
                if sent is True:
                    save_sections.add("recent_photo_continuity")
                self._save_data_sync(sections=save_sections)
                return
        except Exception as exc:
            logger.debug("标注最近生图记录失败: %s", _single_line(exc, 120))

    def _apply_photo_generation_fixed_prompt(self, prompt_text: str) -> str:
        prompt = str(prompt_text or "").strip()
        fixed = _single_line(
            runtime_persona_setting(self, "photo_generation_fixed_prompt", ""), 500
        )
        if not fixed:
            return prompt
        if fixed in prompt:
            return _single_line(prompt, 1800)
        return _single_line(f"{prompt}\n\nAdditional fixed prompt: {fixed}".strip(), 1800)

    @staticmethod
    def _sanitize_photo_generation_fixed_prompt_config(value: Any, *, limit: int = 5000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", text)
        text = re.sub(
            r"</?(?:instruction|system|assistant|user|tool|memorycompanion-context)\b[^>]*>",
            " ",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"(?im)^\s*\[(?:user image request|reference and wardrobe ruling|"
            r"scene, style and final preset|composition and continuity)\]\s*$",
            " ",
            text,
        )
        return _single_line(text, max(0, int(limit or 0)))

    def _photo_generation_workflow_fixed_prompt_section(
        self,
        workflow_kind: str,
    ) -> tuple[PromptSection, dict[str, Any]]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            scope = "edit"
            config_key = "photo_generation_edit_fixed_prompt"
            label = "Additional image-edit fixed prompt"
        elif normalized in {"selfie", "portrait", "自拍", "人像"}:
            scope = "selfie"
            config_key = "photo_generation_selfie_fixed_prompt"
            label = "Additional selfie fixed prompt"
        else:
            scope = "text2img"
            config_key = "photo_generation_text2img_fixed_prompt"
            label = "Additional text-to-image fixed prompt"

        raw = str(runtime_persona_setting(self, config_key, "") or "")
        normalized_prompt = self._sanitize_photo_generation_fixed_prompt_config(raw)
        positive, negative = self._photo_generation_semantic_prompt_parts(normalized_prompt)
        section = prompt_section(
            key=f"photo.workflow_fixed.{scope}",
            title="workflow_fixed_prompt",
            source="photo_prompt_context",
            content=PhotoPromptContent(
                positive=f"{label}: {positive}" if positive else "",
                negative=negative,
                domain_source="fixed_prompt",
                protected=True,
                sanitize_conflicts=True,
            ),
        )
        raw_trimmed = raw.strip()
        audit = {
            "scope": scope,
            "config_key": config_key,
            "configured": bool(raw_trimmed),
            "normalized": bool(normalized_prompt),
            "normalization_changed": raw_trimmed != normalized_prompt,
            "raw_length": len(raw),
            "normalized_length": len(normalized_prompt),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
            if raw
            else "",
            "normalized_sha256": hashlib.sha256(
                normalized_prompt.encode("utf-8", "ignore")
            ).hexdigest()
            if normalized_prompt
            else "",
        }
        return section, audit

    @staticmethod
    def _normalize_photo_generation_prompt_format(value: Any) -> str:
        text = str(value or "traditional").strip().lower().replace("-", "_")
        if text in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion", "nai联动", "nai插件联动"}:
            return "nai"
        if text in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"}:
            return "natural_language"
        return "traditional"

    @staticmethod
    def _normalize_bot_relationship_cards(value: Any) -> list[str]:
        return normalize_bot_relationship_cards(value)

    def _photo_generation_prompt_format_mode(self) -> str:
        return self._normalize_photo_generation_prompt_format(
            runtime_persona_setting(self, "photo_generation_prompt_format", "traditional")
        )

    @staticmethod
    def _normalize_photo_generation_negative_prompt_mode(value: Any) -> str:
        normalized = str(value or "safe_default").strip().lower().replace("-", "_")
        if normalized in {"merge", "append", "custom_merge", "合并", "合并自定义"}:
            return "merge"
        if normalized in {"replace", "override", "custom_replace", "替换", "完全替换"}:
            return "replace"
        return "safe_default"

    def _photo_generation_negative_prompt_mode(self) -> str:
        return self._normalize_photo_generation_negative_prompt_mode(
            runtime_persona_setting(
                self, "photo_generation_negative_prompt_mode", "safe_default"
            )
        )

    @classmethod
    def _sanitize_photo_generation_negative_prompt_config(
        cls,
        value: Any,
        *,
        limit: int = 3000,
    ) -> str:
        raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = cls._sanitize_photo_generation_fixed_prompt_config(
            raw.replace("\n", ", "),
            limit=limit,
        )
        if not text:
            return ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", text, flags=re.I | re.S)
        if negative_match:
            text = negative_match.group(1)
        else:
            text = re.sub(r"^(?:avoid|negative|负面提示词)\s*[：:]?\s*", "", text, flags=re.I)
        values: list[str] = []
        seen: set[str] = set()
        for raw_part in re.split(r"(?:\r?\n+|[,，;；]+)", text):
            part = re.sub(r"\s+", " ", raw_part).strip(" .。")
            if not part:
                continue
            _is_negative, content = cls._photo_generation_negative_clause_content(part)
            content = content or part
            key = content.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(content)
        return _single_line(", ".join(values), limit)

    def _photo_generation_custom_negative_prompt(self, workflow_kind: str) -> str:
        raw_kind = str(workflow_kind or "").strip().lower()
        if raw_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            normalized = "edit"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "sticker", "emoji", "meme", "表情包", "贴纸"}:
            normalized = "selfie"
        else:
            normalized = "text2img"
        scoped_key = {
            "text2img": "photo_generation_text2img_negative_prompt",
            "selfie": "photo_generation_selfie_negative_prompt",
            "edit": "photo_generation_edit_negative_prompt",
        }[normalized]
        values = (
            runtime_persona_setting(self, "photo_generation_negative_prompt", ""),
            runtime_persona_setting(self, scoped_key, ""),
        )
        combined: list[str] = []
        seen: set[str] = set()
        for value in values:
            sanitized = self._sanitize_photo_generation_negative_prompt_config(value)
            for part in (item.strip() for item in sanitized.split(",")):
                key = part.casefold()
                if not part or key in seen:
                    continue
                seen.add(key)
                combined.append(part)
        return _single_line(", ".join(combined), 5000)

    def _apply_photo_generation_negative_prompt_policy(
        self,
        sections: tuple[PromptSection, ...],
        workflow_kind: str,
    ) -> tuple[PromptSection, ...]:
        mode = self._photo_generation_negative_prompt_mode()
        adjusted = list(sections)
        if mode == "replace":
            replaceable_names = {
                "natural_language_contract",
                "daily_outfit_contract",
                "edit_contract",
                "composition",
                "subject_count",
            }
            adjusted = [
                replace(
                    section,
                    content=replace(section.content, negative=""),
                )
                if section.title in replaceable_names
                and isinstance(section.content, PhotoPromptContent)
                and section.content.negative
                else section
                for section in adjusted
            ]
        if mode in {"merge", "replace"}:
            custom_negative = self._photo_generation_custom_negative_prompt(workflow_kind)
            if custom_negative:
                adjusted.append(
                    prompt_section(
                        key="photo.custom_negative_prompt",
                        title="custom_negative_prompt",
                        source="photo_prompt_context",
                        content=PhotoPromptContent(
                            negative=custom_negative,
                            domain_source="fixed_prompt",
                            protected=True,
                            sanitize_conflicts=True,
                        ),
                    )
                )
        return tuple(adjusted)

    def _photo_generation_prompt_format_instruction(self) -> str:
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "使用 NAI（NovelAI 4/4.5）联动写法：以英文 danbooru 风格标签为主、逗号分隔，"
                "精简到能讲清构图即可，不堆砌重复或无意义 tag。"
                "加权用花括号 {tag} 提升、方括号 [tag] 降低，可叠层；也可用 权重::标签:: "
                "对一个或多个标签整体加权（示例 1.5::red dress, long dress::），"
                "可以使用较高权重值（2、5 甚至 10 以上）强调关键元素。"
                "移除物体或翻转概念用负向权重（示例 -1::unwanted object::）；"
                "混合 3 个以上画师风格时可加 -2::artist collaboration:: 降低鬼图概率。"
                "已知二次元角色用 角色名 (作品名) 形式（示例 texas the omertosa (arknights)），"
                "特征不全时多补几个描述词；情绪词有效，可加入增强表情。"
                "多角色（最多 6 名）时每个角色分别用 {人物 [该角色的画风/动作/神态/外貌 tags] 人物} 包裹，"
                "块内两个占位符‘人物’不能删除，可用 {位置中} {位置左} {位置右上} 等位置标签"
                "（5x5 共 25 种）指定站位，角色专属负面词条写 ntags = [tags]；"
                "角色互动动作用 source#/target#/mutual# 前缀（示例 source#hug 发起拥抱，"
                "target#hug 被拥抱，mutual#hug 互相拥抱）。"
                "需要画面英文文字用 Text: 内容, ；不需要文字加 no text, 。"
                "直接输出可投喂生图后端的提示词字符串，不要输出 Positive prompt/Negative prompt 标题，"
                "也不要写解释性段落。"
            )
        if mode == "natural_language":
            return (
                "自然语言描述：连贯具体的英文句子，覆盖主体、外观、动作、场景、光线、镜头、构图与风格；"
                "不要标签堆或权重语法；要避免的内容在末句用 Avoid ... 表达。"
            )
        return (
            "传统文生图写法：英文短词组按主体、外观、服装、场景、光线、镜头、构图、风格排列，逗号分隔；"
            "用 Positive prompt: ... Negative prompt: ... 结构，不写解释段落。"
        )

    def _photo_tool_prompt_format_instruction(self) -> str:
        """Compact format hint for the request-local tool description."""
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "NAI 4/4.5：使用精简的英文 danbooru 标签并以逗号分隔；"
                "用 {tag} 加权、[tag] 降权、1.5::tags:: 数值加权，负权重移除不需要的概念；"
                "已知角色写作 人物名(作品名)，可使用情绪标签；"
                "多角色分别写作 {人物[tags]人物}，互动使用 source#/target#/mutual#。"
                "直接输出可投喂后端的提示词，不加 Positive/Negative 标题或解释。"
                "用户明确给出的画面标签应原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
            )
        if mode == "natural_language":
            return (
                "使用自然语言描述：用连贯具体的英文句子描述主体、外观、动作、场景、光线、镜头、构图与风格；"
                "不要标签堆、权重语法或 Positive/Negative 标题；需要避免的内容在末句用 Avoid ... 表达。"
                "用户明确给出的画面要素应尽量原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
            )
        return (
            "使用英文短词组，按主体、外观、服装、场景、光线、镜头、构图、风格排列并以逗号分隔；"
            "使用 Positive prompt: ... Negative prompt: ... 结构，不写解释。"
            "用户明确给出的画面标签应尽量原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
        )

    @staticmethod
    def _photo_generation_negative_clause_content(clause: str) -> tuple[bool, str]:
        text = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
        if not text:
            return False, ""
        text = re.sub(
            r"^(?:user\s+request|requested\s+final\s+image|用户要求|画面要求)\s*[：:]\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        prefix = re.compile(
            r"^(?:请)?(?:不要|别(?:再)?(?:穿|用|选)?|不想穿|不穿|不用|不是|无需|无须|避免|禁止|不许|不得|排除|拒绝|去掉|脱下|取消)\s*"
            r"|^(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+",
            flags=re.I,
        )
        match = prefix.match(text)
        if match:
            return True, text[match.end():].strip(" ,.;；。，")
        postfix = re.compile(
            r"\s*(?:不要(?:了)?|别穿|不穿|不用|算了|就算了|除外|排除|取消|not|no)\s*$",
            flags=re.I,
        )
        match = postfix.search(text)
        if match:
            return True, text[:match.start()].strip(" ,.;；。，")
        return False, text

    @classmethod
    def _photo_generation_semantic_prompt_parts(cls, prompt_text: str) -> tuple[str, str]:
        """Separate positive request clauses from explicit exclusions without losing mixed requests."""
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if positive_match:
            positive_raw = positive_match.group(1).strip()
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            negative_raw = negative_match.group(1).strip() if negative_match else ""
        else:
            positive_raw = prompt
            negative_raw = ""

        positive_parts: list[str] = []
        negative_parts: list[str] = []

        def add_clause(raw_clause: str) -> None:
            clause = re.sub(r"\s+", " ", str(raw_clause or "")).strip(" ,.;；。，")
            if not clause:
                return
            is_negative, content = cls._photo_generation_negative_clause_content(clause)
            if is_negative:
                transition = re.search(
                    r"(?:但|而|不过|可是)?(?:改穿|换成|换上|换为|改为|要穿|穿上|而要)"
                    r"|\b(?:but|instead|and)\s+(?:wear|change\s+into|switch\s+to|put\s+on)\b",
                    content,
                    flags=re.I,
                )
                if transition and transition.start() > 0:
                    excluded = content[:transition.start()].strip(" ,.;；。，")
                    requested = content[transition.start():].strip(" ,.;；。，")
                    if excluded:
                        negative_parts.append(excluded)
                    if requested:
                        positive_parts.append(requested)
                    return
                if content:
                    negative_parts.append(content)
                return
            if content:
                positive_parts.append(content)

        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", positive_raw):
            add_clause(clause)
        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", negative_raw):
            cleaned = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
            if not cleaned:
                continue
            _, content = cls._photo_generation_negative_clause_content(cleaned)
            if content:
                negative_parts.append(content)

        return ", ".join(dict.fromkeys(positive_parts)), ", ".join(dict.fromkeys(negative_parts))

    def _apply_photo_generation_prompt_format(
        self,
        prompt_text: str,
        *,
        prompt_format: str = "",
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        mode = (
            self._normalize_photo_generation_prompt_format(prompt_format)
            if prompt_format
            else self._photo_generation_prompt_format_mode()
        )
        if mode == "nai":
            # Preserve NovelAI inline syntax ({}/[], weight::tags::, multi-character blocks) as authored.
            positive_match = re.search(
                r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
                prompt,
                flags=re.I | re.S,
            )
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            if positive_match or negative_match:
                if positive_match:
                    positive_raw = positive_match.group(1).strip()
                elif negative_match:
                    positive_raw = prompt[:negative_match.start()].strip(" \t\r\n,;。；")
                else:
                    positive_raw = prompt
                negative_raw = negative_match.group(1).strip(" \t\r\n.,;!?。；！") if negative_match else ""
                separator = ", " if positive_raw and negative_raw else ""
                prompt = positive_raw + (f"{separator}-1.5::{negative_raw}::" if negative_raw else "")
            return self._photo_prompt_clip(prompt, 2400, preserve_tail=True)
        positive, negative = self._photo_generation_semantic_prompt_parts(prompt)
        positive = positive or "the requested image"
        if mode == "natural_language":
            natural = f"Create a single coherent image showing {positive}."
            if negative:
                natural += f" Avoid {negative}."
            return self._photo_prompt_clip(natural, 6000, preserve_tail=True)
        formatted = f"Positive prompt: {positive}."
        if negative:
            formatted += f" Negative prompt: {negative}."
        return self._photo_prompt_clip(formatted, 6000, preserve_tail=True)
