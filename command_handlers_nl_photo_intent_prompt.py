# -*- coding: utf-8 -*-
"""自然语言出图意图与提示域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 618 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import re
from .constants import DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
from .conversation_prompt_section import PhotoPromptContent, PromptSection, prompt_section
from .helpers import _now_ts, _path_text, _photo_group_request_matches, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from typing import Any



class CommandHandlersNlPhotoIntentPromptMixin:
    """自然语言出图意图与提示域（从 CommandHandlersMixin 拆出）。"""


    def _natural_language_photo_explicit_plugin_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        plugin_hit = any(
            token in compact
            for token in (
                "插件能力",
                "插件生图",
                "插件画图",
                "用插件",
                "走插件",
                "陪伴能力",
                "陪伴插件",
                "本插件",
            )
        )
        if not plugin_hit:
            return False
        return any(
            token in compact
            for token in (
                "画",
                "绘图",
                "生图",
                "出图",
                "生成图",
                "生成图片",
                "图片",
                "照片",
                "改图",
                "修图",
                "重绘",
            )
        )

    def _natural_language_photo_disabled_text(self, reason: str = "natural_off") -> str:
        if reason == "photo_off":
            return (
                "插件的主动拍照/生图总开关现在没开，所以不能走插件生图链路。\n"
                "位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图。"
            )
        return (
            "插件的规则快判生图/改图入口现在没开，所以不会在主链前直接接管这句。\n"
            "如果想让普通聊天触发生图，建议使用 tool_first 模式，由主链调用 pc_generate_photo；位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图详情 -> 非指令生图/改图。"
        )

    def _natural_language_photo_intent(
        self,
        text: str,
        *,
        has_reference: bool = False,
        directed: bool = False,
    ) -> dict[str, Any]:
        raw = re.sub(r"\[CQ:image,[^\]]+\]", "", str(text or ""))
        raw = re.sub(r"\[CQ:at,[^\]]+\]", "", raw)
        raw = re.sub(r"\[(?:At|@):[^\]]+\]", "", raw, flags=re.I)
        raw = re.sub(r"\[(?:引用消息|回复消息|reply)\]", "", raw, flags=re.I)
        raw = _single_line(raw, 800)
        if not raw:
            return {}
        compact = re.sub(r"\s+", "", raw)
        selfie_markers = (
            "自拍",
            "拍照",
            "拍张照",
            "拍一张照",
            "拍一张照片",
            "拍张照片",
            "来张自拍",
            "发张自拍",
            "发一张自拍",
            "腿照",
            "脚照",
            "手照",
            "全身照",
            "半身照",
            "近照",
            "生活照",
            "穿搭照",
        )
        character_photo_matcher = getattr(self, "_character_photo_request_matches", None)
        selfie_hit = any(marker in compact for marker in selfie_markers) or bool(
            callable(character_photo_matcher) and character_photo_matcher(raw)
        )
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(raw)
        draw_visual_targets = ("图片", "照片", "插画", "头像", "壁纸", "表情包", "自拍", "拍照", "画卷", "图")
        edit_visual_targets = draw_visual_targets + ("这张", "这个图", "引用图")
        edit_strong_markers = ("改图", "修图", "重绘", "p图", "P图", "p一下", "P一下")
        edit_operation_markers = ("改成", "改为", "改一下", "p成", "P成", "换成", "变成", "加上", "加个", "去掉", "去除")
        draw_patterns = (
            r"(?:帮我|给我|替我|请你|麻烦你)?(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?.{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画一张|画个|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画|生成|做|出).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成一张|生一张|做一张|做个|出一张).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:来|整)(?:一张|张|个).{0,40}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
        )
        draw_hit = any(re.search(pattern, raw, flags=re.I) for pattern in draw_patterns)
        if draw_hit and not any(token in compact for token in draw_visual_targets):
            draw_hit = False
        if selfie_hit and directed:
            draw_hit = True
        if not draw_hit and explicit_plugin_request:
            draw_hit = True
        if not draw_hit and directed:
            bare_draw_patterns = (
                r"^(?:帮我|给我|替我|请你|请|麻烦你)?(?:画一张|画个|画一下|画一个|画|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个)\S{1,120}",
                r"^(?:帮我|给我|替我|请你|请|麻烦你)(?:画|生成|做|出|整)\S{1,120}",
            )
            draw_hit = any(re.search(pattern, compact, flags=re.I) for pattern in bare_draw_patterns)
            if draw_hit and re.search(r"(?:画个饼|画饼|规划|画重点|画大饼|画风|图个|图啥|图什么)", compact, flags=re.I):
                draw_hit = False
        edit_hit = False
        if has_reference:
            explicit_visual_target = any(token in compact for token in edit_visual_targets)
            strong_edit = any(marker in compact for marker in edit_strong_markers)
            operation_edit = any(marker in compact for marker in edit_operation_markers)
            leading_operation = any(compact.startswith(marker) for marker in edit_operation_markers)
            implicit_directed_edit = bool(
                directed
                and not re.search(r"(?:什么|怎么|为啥|为什么|吗|呢|？|\?)", compact)
                and any(
                    marker in compact
                    for marker in (
                        "红色",
                        "蓝色",
                        "绿色",
                        "黑色",
                        "白色",
                        "粉色",
                        "紫色",
                        "黄色",
                        "基调",
                        "色调",
                        "风格",
                        "背景",
                        "滤镜",
                        "清晰",
                        "高清",
                        "二次元",
                        "写实",
                        "赛博",
                    )
                )
            )
            edit_hit = bool(strong_edit or (operation_edit and (explicit_visual_target or leading_operation)) or implicit_directed_edit)
        if not draw_hit and not edit_hit:
            return {}
        prompt = raw
        cleanup_patterns = [
            r"^(?:麻烦|可以|能不能|能|帮我|给我|替我|请你|请)?",
            r"^(?:拍一张|拍张|拍个|拍一下|发一张|发张|来一张|来张)(?:自拍|照片|照|图片|图)?",
            r"^(?:用|走)?(?:这个|你|本)?(?:插件能力|插件|陪伴能力|陪伴插件)(?:来|去)?",
            r"^(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:画一张|画个|画一下|画一个|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|图)?",
            r"^(?:把)?(?:这张图|这个图|这张|引用图|图片)?(?:帮我)?(?:改成|改为|改一下|改图|修图|重绘|p成|P成|换成|变成)",
        ]
        for pattern in cleanup_patterns:
            prompt = re.sub(pattern, "", prompt, count=1, flags=re.I).strip()
        prompt = prompt.strip(" ，,。.!！?？:：；;")
        if selfie_hit and prompt in {"", "看看", "看一下", "看看吧", "看看嘛"}:
            prompt = "拍一张自拍"
        if not prompt or prompt in {"图", "图片", "一张图", "这张", "这张图"}:
            return {
                "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
                "prompt": "",
                "needs_prompt": True,
            }
        return {
            "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
            "prompt": _single_line(prompt, 700),
            "raw": raw,
        }

    def _natural_language_photo_quota_left(self, user: dict[str, Any]) -> int:
        limit = max(0, _safe_int(runtime_persona_setting(self, 'natural_language_photo_generation_max_daily', 0), 0))
        if limit <= 0:
            return 0
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        used = _safe_int(user.get("natural_photo_generated_today"), 0)
        if str(user.get("natural_photo_generated_day") or "") != today:
            used = 0
        return max(0, limit - used)

    def _note_natural_language_photo_generation_attempt(self, user: dict[str, Any], image_path: str = "") -> None:
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        if user.get("natural_photo_generated_day") != today:
            user["natural_photo_generated_day"] = today
            user["natural_photo_generated_today"] = 0
        user["natural_photo_generated_today"] = _safe_int(user.get("natural_photo_generated_today"), 0) + 1
        user["last_natural_photo_path"] = _path_text(image_path, 1000)
        user["last_natural_photo_at"] = _now_ts()

    def _command_photo_generation_daily_limit(self) -> int:
        return _safe_int(
            runtime_persona_setting(self, 'command_photo_generation_max_daily', -1),
            -1,
            -1,
            100,
        )

    def _command_photo_quota_block_message(self) -> str:
        if self._command_photo_generation_daily_limit() == 0:
            return "管理员已关闭用户请求生图/改图（“用户请求生图每日上限”为 0）。"
        return "今天用户请求生图/改图额度用完了。管理员可调高“用户请求生图每日上限”，或设为 -1 取消每日限制。"

    def _command_photo_quota_left(self, user: dict[str, Any]) -> int | None:
        limit = self._command_photo_generation_daily_limit()
        if limit < 0:
            return None
        if limit == 0:
            return 0
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        used = _safe_int(user.get("command_photo_generated_today"), 0)
        if str(user.get("command_photo_generated_day") or "") != today:
            used = 0
        return max(0, limit - used)

    def _note_command_photo_generation_attempt(self, user: dict[str, Any], image_path: str = "") -> None:
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        if user.get("command_photo_generated_day") != today:
            user["command_photo_generated_day"] = today
            user["command_photo_generated_today"] = 0
        user["command_photo_generated_today"] = _safe_int(user.get("command_photo_generated_today"), 0) + 1
        user["last_command_photo_path"] = _path_text(image_path, 1000)
        user["last_command_photo_at"] = _now_ts()

    @staticmethod
    def _natural_photo_prompt_has_explicit_people_request(prompt: Any) -> bool:
        text = _single_line(prompt, 1200).lower()
        if not text:
            return False
        if any(
            marker in text
            for marker in (
                "人物",
                "角色",
                "女孩",
                "少女",
                "女人",
                "男人",
                "男生",
                "女生",
                "男孩",
                "小孩",
                "人群",
                "路人",
                "游客",
                "行人",
                "背影",
            )
        ):
            return True
        return bool(
            re.search(
                r"\b(?:person|people|girl|woman|boy|man|human|character|crowd|pedestrian|tourist)s?\b",
                text,
                flags=re.I,
            )
        )

    @staticmethod
    def _natural_photo_prompt_has_explicit_back_view_request(prompt: Any) -> bool:
        text = _single_line(prompt, 1200).lower()
        if not text:
            return False
        return bool(
            any(marker in text for marker in ("背影", "背对镜头", "背对相机", "从背后", "身后视角"))
            or re.search(r"\b(?:back[-\s]?view|from\s+behind|facing\s+away)\b", text, flags=re.I)
        )

    def _build_natural_language_photo_prompt(
        self,
        *,
        prompt: str,
        kind: str,
        has_reference: bool,
        memory_context: str = "",
    ) -> str:
        sections = self._build_natural_language_photo_prompt_sections(
            prompt=prompt,
            kind=kind,
            has_reference=has_reference,
            memory_context=memory_context,
        )
        combined_positive = ", ".join(
            section.content.positive
            for section in sections
            if isinstance(section.content, PhotoPromptContent)
            and section.content.positive
        )
        combined_negative = ", ".join(
            section.content.negative
            for section in sections
            if isinstance(section.content, PhotoPromptContent)
            and section.content.negative
        )
        return _single_line(
            "Positive prompt: "
            + combined_positive
            + ". Negative prompt: "
            + combined_negative
            + ".",
            6500,
        )

    def _build_natural_language_photo_prompt_sections(
        self,
        *,
        prompt: str,
        kind: str,
        has_reference: bool,
        memory_context: str = "",
    ) -> tuple[PromptSection, ...]:
        style_name, style_instruction = self._get_photo_style_instruction() if callable(getattr(self, "_get_photo_style_instruction", None)) else ("默认", "")
        style_prompt = (
            self._photo_style_prompt_en(style_name, style_instruction)
            if callable(getattr(self, "_photo_style_prompt_en", None))
            else (_single_line(style_instruction, 220) or _single_line(style_name, 40) or "natural image style")
        )
        extra_prompt = str(
            runtime_persona_setting(self, 'natural_language_photo_extra_prompt', DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT)
            or ""
        ).strip()
        visual_memory = self._visual_photo_memory_context(memory_context)
        explicit_people_request = self._natural_photo_prompt_has_explicit_people_request(prompt)
        explicit_back_view_request = self._natural_photo_prompt_has_explicit_back_view_request(prompt)
        referenced_group_request = bool(has_reference and _photo_group_request_matches(prompt))
        if kind not in {"edit", "selfie"} and style_name == "二次元" and not explicit_people_request:
            style_prompt = (
                "2D anime illustration style, detailed environment and object art, "
                "cel-shaded rendering, soft colors, slice-of-life atmosphere"
            )
        if kind == "edit" and has_reference:
            user_request = _single_line(prompt, 420) or "edit the reference image"
            positive = [
                "image edit based on the provided reference image",
                "the provided image is the sole visual reference and source canvas",
                "this is an image editing task, not a selfie or new portrait generation request",
                "preserve unchanged subjects, composition, identity, clothing, and important details",
                "only modify the parts explicitly requested by the user",
                "do not replace any person with the assistant persona or today's outfit",
                style_prompt,
            ]
            negative = [
                "unrequested identity change",
                "unrequested outfit change",
                "changed composition",
                "extra people",
                "text",
                "watermark",
                "logo",
                "nsfw",
            ]
        elif kind == "selfie":
            user_request = _single_line(prompt, 420) or "take a selfie"
            if referenced_group_request:
                identity_continuity = (
                    "preserve every referenced person's identity, count, relative placement, and stable appearance from the explicitly supplied source image"
                )
                positive = [
                    "multi-person photo based only on the explicitly supplied source reference",
                    "preserve every referenced person and do not invent anyone else",
                    "one continuous scene",
                    "natural expressions and interaction",
                    "clear faces when visible in the source",
                    "natural snapshot composition",
                    "soft natural light",
                    style_prompt,
                ]
            elif explicit_back_view_request:
                identity_continuity = (
                    "preserve character identity and stable appearance from the selected reference image"
                    if has_reference
                    else "preserve character identity and stable appearance from available visual continuity"
                )
                positive = [
                    "single character environmental portrait",
                    "solo",
                    "the requested back view is intentional",
                    "complete head and hair",
                    "recognizable hairstyle silhouette and stable character appearance",
                    "outfit and surrounding scene visible",
                    "natural environmental composition",
                    "soft natural light",
                    style_prompt,
                ]
            else:
                identity_continuity = (
                    "preserve character identity and stable appearance from the selected reference image"
                    if has_reference
                    else "preserve character identity and stable appearance from available visual continuity"
                )
                positive = [
                    "single character selfie",
                    "solo",
                    "visible face",
                    "complete head and hair",
                    "clear eyes",
                    "natural expression",
                    "upper body or outfit visible",
                    "natural phone snapshot",
                    "centered composition",
                    "soft natural light",
                    style_prompt,
                ]
            negative = [
                "cropped head",
                "headless",
                "body only",
                "outfit only",
                "bad hands",
                "extra fingers",
                "text",
                "watermark",
                "logo",
                "unreferenced extra people" if referenced_group_request else "other people",
                "nsfw",
            ]
            if not explicit_back_view_request and not referenced_group_request:
                negative.extend(["faceless", "face hidden", "back view"])
        else:
            user_request = _single_line(prompt, 520)
            positive = [
                "generate an image from the user request",
                "clear main subject",
                "concrete scene",
                "natural lighting",
                "clean composition",
                "no private screen",
                style_prompt,
            ]
            negative = [
                "vague empty scene",
                "unrelated subject",
                "private information",
                "text",
                "watermark",
                "logo",
                "nsfw",
            ]
            if not explicit_people_request:
                positive.append(
                    "show only the requested scene or object; do not add any unrequested person, character, or visible photographer"
                )
                negative.extend(
                    [
                        "unrequested person",
                        "unrequested character",
                        "visible photographer",
                        "back-view person",
                    ]
                )
        sections = [
            prompt_section(
                key="photo.command.user_request",
                title="user_request",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    positive=f"user request: {user_request}",
                    domain_source="user_request",
                    protected=True,
                ),
            )
        ]
        sections.append(
            prompt_section(
                key="photo.command.natural_language_contract",
                title="natural_language_contract",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    # This is a trusted workflow contract, not caller-supplied
                    # visual memory. Freeze it for this task so the N-1 resolver
                    # cannot trim safety/composition rules as ambient context.
                    positive=_single_line(
                        ", ".join(
                            part for part in positive if _single_line(part, 520)
                        ),
                        1400,
                    ),
                    negative=_single_line(", ".join(negative), 760),
                    domain_source="fixed_prompt",
                    protected=True,
                ),
            )
        )
        if kind == "selfie":
            sections.append(
                prompt_section(
                    key="photo.command.identity_continuity",
                    title="identity_continuity",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=identity_continuity,
                        domain_source="visual_memory",
                    ),
                )
            )
        if visual_memory and kind != "edit":
            sections.append(
                prompt_section(
                    key="photo.command.visual_memory",
                    title="visual_memory",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"visual continuity reference: {_single_line(visual_memory, 300)}",
                        domain_source="visual_memory",
                    ),
                )
            )
        if extra_prompt:
            sections.append(
                prompt_section(
                    key="photo.command.natural_language_extra",
                    title="natural_language_extra",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"additional generation preference: {_single_line(extra_prompt, 420)}",
                        domain_source="fixed_prompt",
                    ),
                )
            )
        return tuple(sections)

    @staticmethod
    def _photo_generation_workflow_kind(intent_kind: str) -> str:
        normalized = str(intent_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            return "edit"
        if normalized in {"selfie", "portrait", "自拍", "人像", "sticker", "emoji", "meme", "表情包", "贴纸"}:
            return "selfie"
        return "text2img"

    def _visual_photo_memory_context(self, memory_context: str, *, limit: int = 520) -> str:
        raw = str(memory_context or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"<instruction\b[^>]*>.*?(?:</instruction>|$)", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"</?(?:MemoryCompanion-Context|memory_companion_context)\b[^>]*>", " ", raw, flags=re.I)
        raw = re.sub(r"<[^>\n]{0,120}>", " ", raw)
        raw = raw.replace("RememberYou", "我会牢牢记住你")
        reject_tokens = (
            "MemoryCompanion",
            "memory_companion",
            "instruction",
            "固定分工",
            "persona_memory",
            "open_loops",
            "promise",
            "relationship",
            "emotional",
            "facts",
            "不是用户新发言",
            "不是新的回复任务",
            "先回应",
            "不要让旧话题",
            "按 ",
            "分区理解",
            "只影响语气",
            "不确定内容",
            "必须带不确定",
            "需要避免",
            "用户常问",
            "今日穿搭生成",
            "历史穿搭",
        )
        visual_tokens = (
            "穿搭",
            "衣",
            "裙",
            "外套",
            "上衣",
            "裤",
            "鞋",
            "袜",
            "配饰",
            "发夹",
            "发型",
            "发色",
            "头发",
            "瞳色",
            "眼睛",
            "表情",
            "脸",
            "自拍",
            "照片",
            "参考图",
            "颜色",
            "色调",
            "风格",
            "背景",
            "地点",
            "位置",
            "室内",
            "室外",
            "家里",
            "学校",
            "咖啡",
            "房间",
            "街",
            "公园",
            "天气",
        )
        parts = re.split(r"[\n\r。；;|｜]+", raw)
        kept: list[str] = []
        for part in parts:
            item = _single_line(part, 120)
            if not item:
                continue
            if any(token in item for token in reject_tokens):
                continue
            if not any(token in item for token in visual_tokens):
                continue
            item = re.sub(r"^(?:[-*·•]\s*|\d+[.、]\s*)", "", item).strip()
            item = re.sub(r"^(?:我会牢牢记住你|RememberYou|MemoryCompanion)\s*(?:相关)?(?:记忆|参考)?[：:]\s*", "", item, flags=re.I).strip()
            item = re.sub(r"^(?:记忆|相关记忆|参考|内容|摘要)[：:]\s*", "", item).strip()
            if item and item not in kept:
                kept.append(_single_line(item, 90))
            if len(kept) >= 5:
                break
        return _single_line("；".join(kept), limit)
