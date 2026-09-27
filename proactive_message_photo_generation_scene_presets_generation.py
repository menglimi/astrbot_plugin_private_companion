# -*- coding: utf-8 -*-
"""场景预设解析与出图执行域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 674 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import (
    PromptDocument,
    PromptDocumentPart,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
)
from .helpers import _path_text, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from .proactive_message_photo_generation_shared import PhotoGenerationResult, logger
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from typing import Any



class ProactiveMessagePhotoGenerationScenePresetsGenerationMixin:
    """场景预设解析与出图执行域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _builtin_photo_generation_scene_presets(self) -> dict[str, str]:
        return {
            "角色自拍": (
                "natural casual character photo, single character, face visible by default, clear face, hair, expression, neck and shoulders, "
                "phone snapshot feeling, lifelike composition, no cropped head, no hidden face or back view unless explicitly requested, no body-only framing"
            ),
            "COS自拍": (
                "cosplay themed selfie, keep the character's own face, hair color, eye color, and key visual traits, "
                "clear costume theme, tasteful outfit, convention snapshot or room fitting photo feeling"
            ),
            "日常穿搭": (
                "daily outfit portrait without mirror, exactly one character wearing one coherent outfit in one continuous frame, "
                "no outfit comparison, no split screen, no side-by-side panels, handheld selfie or natural environmental portrait, "
                "upper-body to three-quarter framing, visible face, clear clothing layers and color palette, "
                "location-appropriate background, no phone covering face, not body-only"
            ),
            "居家睡衣": (
                "sleepwear or bedtime loungewear portrait matching the explicit clothing request and selected reference, "
                "exactly one coherent sleepwear outfit, preserve the character identity, natural home or bedtime context, "
                "do not restore a daytime outfit, coat, school uniform, or commuter layers unless explicitly requested"
            ),
            "居家服": (
                "comfortable homewear portrait, one coherent relaxed indoor outfit, natural home activity and lived-in setting, "
                "preserve the character identity and selected homewear reference, no commuter coat or formal layers unless requested"
            ),
            "校服人像": (
                "school-uniform portrait matching the explicit request, one coherent uniform with consistent layers and colors, "
                "natural school or campus context, preserve the character identity, not cosplay unless explicitly requested"
            ),
            "礼服人像": (
                "formalwear portrait matching the explicit request, one coherent formal outfit with consistent silhouette and materials, "
                "location-appropriate formal context, preserve the character identity, no casual or sportswear substitution"
            ),
            "泳装人像": (
                "swimwear portrait matching the explicit request, one coherent swim outfit, appropriate pool or beach context, "
                "preserve the character identity, tasteful natural composition, no unrelated daytime clothing layers"
            ),
            "运动服人像": (
                "sportswear portrait matching the explicit request, one coherent practical athletic outfit, natural activity setting, "
                "preserve the character identity, no formalwear or commuter outfit substitution"
            ),
            "镜前穿搭": (
                "explicitly requested mirror outfit photo, half-body to three-quarter mirror composition, "
                "clear clothes, jacket, accessories and color palette, complete visible face, no phone covering face, "
                "subject inside square safe area, not full-length body-only, not outfit-only, not clothing close-up"
            ),
            "头像特写": (
                "avatar-ready face close-up, clear hair, eyes and expression, clean background, centered face, enough margin, "
                "no text, no watermark, no cluttered props"
            ),
            "房间日常": (
                "indoor slice-of-life photo, natural desk objects, books, cup, window side or bedside details, "
                "one clear subject, calm lived-in atmosphere, avoid overcrowded composition"
            ),
            "可拍画面": (
                "casual photo shared with a close friend, concrete visual subject, natural lighting, not a vague landscape, "
                "not a weather report, when the request is scenery or an object frame it from the photographer's point of view, "
                "do not insert an unrequested person, character, visible photographer, or back-view figure, "
                "no private screen, no real personal information, no unrelated text, no watermark"
            ),
            "表情包场景": (
                "single sticker-like image for chat, clear emotion, simple composition, cute exaggerated expression, "
                "character remains recognizable, only include short text if the user explicitly requested it"
            ),
        }

    def _parse_photo_generation_scene_presets(self, raw: Any) -> dict[str, str]:
        presets: dict[str, str] = {}
        if isinstance(raw, dict):
            iterable = raw.items()
            for key, value in iterable:
                name = _single_line(key, 40)
                prompt = _single_line(value, 900)
                if name and prompt:
                    presets[name] = prompt
            return presets
        items: list[Any] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            text = raw.replace("\r\n", "\n").replace("\r", "\n")
            items = [line for line in text.split("\n") if str(line or "").strip()]
        for item in items:
            if isinstance(item, dict):
                name = _single_line(item.get("name") or item.get("key") or item.get("title"), 40)
                prompt = _single_line(item.get("prompt") or item.get("value") or item.get("content"), 900)
            else:
                text = str(item or "").strip()
                if ":" in text:
                    name, prompt = text.split(":", 1)
                elif "：" in text:
                    name, prompt = text.split("：", 1)
                else:
                    continue
                name = _single_line(name, 40)
                prompt = _single_line(prompt, 900)
            if name and prompt:
                presets[name] = prompt
        return presets

    def _photo_generation_scene_presets(self) -> dict[str, str]:
        presets = self._builtin_photo_generation_scene_presets()
        presets.update(
            self._parse_photo_generation_scene_presets(
                runtime_persona_setting(self, "photo_generation_scene_presets", "")
            )
        )
        return presets

    def _apply_photo_generation_scene_presets(
        self,
        prompt_text: str,
        workflow_kind: str,
        *,
        preset_names: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        prompt = str(prompt_text or "").strip()
        presets = self._photo_generation_scene_presets()
        requested_names = preset_names or []
        names = [name for name in requested_names if name in presets][:1]
        if not names:
            return _single_line(prompt, 1800), []
        blocks = []
        for name in names:
            content = _single_line(presets.get(name), 900)
            if content and content not in prompt:
                blocks.append(f"{self._photo_generation_scene_preset_label_en(name)}: {content}")
        if not blocks:
            return _single_line(prompt, 1800), names
        merged = f"{prompt}\n\nScene preset: " + "; ".join(blocks)
        return _single_line(merged, 1800), names

    def _photo_generation_scene_preset_label_en(self, name: str) -> str:
        return {
            "角色自拍": "casual character selfie",
            "COS自拍": "cosplay selfie",
            "日常穿搭": "daily outfit portrait",
            "居家睡衣": "home sleepwear portrait",
            "居家服": "comfortable homewear portrait",
            "校服人像": "school uniform portrait",
            "礼服人像": "formalwear portrait",
            "泳装人像": "swimwear portrait",
            "运动服人像": "sportswear portrait",
            "镜前穿搭": "mirror outfit photo",
            "头像特写": "avatar close-up",
            "房间日常": "indoor slice-of-life",
            "可拍画面": "casual shareable photo",
            "表情包场景": "sticker scene",
        }.get(_single_line(name, 40), _single_line(name, 40) or "scene preset")

    async def _generate_photo_image(
        self,
        **kwargs: Any,
    ) -> tuple[str, str, str]:
        """Run image generation through the selected backend service."""
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected(kwargs.get("workflow_kind", "")):
            nai_bridge = getattr(self, "_nai_image_generate", None)
            if callable(nai_bridge):
                return await nai_bridge(**kwargs)
            return (
                "NAI 生图",
                "",
                "生图后端已选择 NAI 直连，但未检测到 NAI 生图插件，请安装并启用 astrbot_plugin_nai_image。",
            )
        bridge = getattr(self, "_image_companion_generate", None)
        if callable(bridge):
            return await bridge(**kwargs)
        return (
            "独立生图服务",
            "",
            "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
        )

    async def _generate_photo_image_result(self, **kwargs: Any) -> PhotoGenerationResult:
        self._image_companion_generation_metadata = {}
        self._nai_image_generation_metadata = {}
        backend, image_path, note = await self._generate_photo_image(**kwargs)
        metadata: dict[str, Any] = {}
        bridge_metadata_supported = False
        for getter_name in ("_image_companion_last_metadata", "_nai_image_last_metadata"):
            getter = getattr(self, getter_name, None)
            if callable(getter):
                bridge_metadata_supported = True
                metadata = getter() or {}
                if metadata:
                    break
        if not metadata and not bridge_metadata_supported:
            metadata = self._photo_generation_result_metadata(
                image_path=image_path,
                session_key=_single_line(kwargs.get("session_key"), 340),
            )
        reference_path = _path_text(
            metadata.get("reference_path") or kwargs.get("reference_image_path"),
            1000,
        )
        intent_metadata = metadata.get("reference_intent") if isinstance(metadata.get("reference_intent"), dict) else {}
        plan_metadata = metadata.get("reference_plan") if isinstance(metadata.get("reference_plan"), dict) else {}
        fallback_metadata = metadata.get("reference_fallback") if isinstance(metadata.get("reference_fallback"), dict) else {}
        return PhotoGenerationResult(
            backend=_single_line(backend, 80),
            image_path=_path_text(image_path, 1000),
            note=_single_line(note, 500),
            trace_id=_single_line(metadata.get("trace"), 40),
            reference_selected_path=reference_path,
            reference_used=bool(metadata.get("reference_used")),
            reference_id=_single_line(metadata.get("reference_id"), 60),
            reference_kind=_single_line(metadata.get("reference_kind"), 40),
            reference_roles=tuple(
                _single_line(role, 40)
                for role in (metadata.get("reference_roles") or [])
                if _single_line(role, 40)
            ),
            wardrobe_mode=_single_line(metadata.get("wardrobe_mode"), 40),
            wardrobe_category=_single_line(metadata.get("wardrobe_category"), 40),
            outfit_locked=bool(metadata.get("outfit_locked")),
            daily_outfit_removed=bool(metadata.get("daily_outfit_removed")),
            preset_names=tuple(
                _single_line(name, 60)
                for name in (metadata.get("presets") or [])
                if _single_line(name, 60)
            )[:1],
            preset_hint=_single_line(metadata.get("preset_hint"), 80),
            preset_source=_single_line(metadata.get("preset_source"), 40),
            suggestion_status=_single_line(metadata.get("suggestion_status"), 60),
            prompt_hash=_single_line(metadata.get("prompt_hash"), 80),
            prompt_path=_path_text(metadata.get("prompt_path"), 1000),
            reference_requested_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("requested_roles") or [])
                if _single_line(role, 40)
            ),
            reference_excluded_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("excluded_roles") or [])
                if _single_line(role, 40)
            ),
            continuity_mode=_single_line(intent_metadata.get("continuity_mode"), 30) or "ambiguous",
            reference_confidence=_safe_float(intent_metadata.get("confidence"), 0.0, 0.0, 1.0),
            reference_plan=tuple(
                dict(binding)
                for binding in (plan_metadata.get("bindings") or [])
                if isinstance(binding, dict)
            ),
            reference_fulfilled_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("fulfilled_roles") or [])
                if _single_line(role, 40)
            ),
            reference_missing_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("missing_roles") or [])
                if _single_line(role, 40)
            ),
            reference_fallback_message=_single_line(fallback_metadata.get("message"), 260),
            generation_completed=bool(metadata.get("generation_completed")),
            failure_stage=_single_line(metadata.get("failure_stage"), 60),
        )

    @staticmethod
    def _photo_scene_generation_prompt_document(
        *,
        persona: str,
        recipient_name: str,
        scene_context: str,
        topic_hint: str,
        motive_hint: str,
        relationship_section: PromptSection | None,
        birthday_rule: str,
        content_options: str,
        style_name: str,
        style_instruction: str,
        prompt_format_instruction: str,
        reason: str,
    ) -> PromptDocument:
        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.photo_scene.task",
                title="主动生活图片提示词生成",
                source="proactive_message",
                content="请根据 AstrBot 默认人格和主动原因,生成一张要通过生图后端制作的“社交媒体随手拍/自拍/生活碎片图”提示词。",
            ), mode=PromptRenderMode.BODY_ONLY),
            prompt_section(
                key="background.photo_scene.persona",
                title="人格",
                source="proactive_message",
                content=persona,
            ),
            prompt_section(
                key="background.photo_scene.recipient",
                title="收信人",
                source="proactive_message",
                content=recipient_name,
            ),
            prompt_section(
                key="background.photo_scene.snapshot",
                title="当前统一情境快照",
                source="proactive_message",
                content=(
                    f"{scene_context}\n"
                    "使用方式：这是当前事实和连续性参考。优先保持时间、地点、日程和情绪互相一致；"
                    "今日穿搭只在本次没有新的服装请求时用于连续性。若话题、动机或画面需求明确要求睡衣、居家服、礼服、COS 等服装变化，"
                    "以本次明确请求为准，不要被今日穿搭覆盖。它只帮助选择自然画面，不要求把所有字段都画出来或写进配文。"
                ),
            ),
            prompt_section(
                key="background.photo_scene.hook",
                title="这次想分享的画面钩子",
                source="proactive_message",
                content=(
                    f"话题：{topic_hint or '（未指定）'}\n"
                    f"那一刻的小动机：{motive_hint or '（未指定）'}"
                ),
            ),
        ]
        if relationship_section is not None:
            sections.append(relationship_section)
        sections.extend(
            (
                prompt_section(
                    key="background.photo_scene.birthday",
                    title="生日卡特殊规则",
                    source="proactive_message",
                    content=birthday_rule,
                ),
                prompt_section(
                    key="background.photo_scene.options",
                    title="内容选择菜单",
                    source="proactive_message",
                    content=content_options,
                ),
                prompt_section(
                    key="background.photo_scene.style",
                    title="生图风格",
                    source="proactive_message",
                    content=f"{style_name}\n风格要求：{style_instruction}",
                ),
                prompt_section(
                    key="background.photo_scene.format",
                    title="提示词表达方式",
                    source="proactive_message",
                    content=prompt_format_instruction,
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.reason",
                    title="主动原因",
                    source="proactive_message",
                    content=f"主动原因：{reason}",
                ), mode=PromptRenderMode.BODY_ONLY),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.output",
                    title="输出 JSON",
                    source="proactive_message",
                    content=(
                        "{\n"
                        '  "kind": "selfie 或 text2img；自拍/人像用 selfie,其他随手拍用 text2img",\n'
                        '  "use_persona_reference": true,\n'
                        '  "prompt": "按上方提示词表达方式输出的英文生图提示词",\n'
                        '  "caption": "图片完成后可转述给最终私聊模型的一句话画面描述"\n'
                        "}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "1. 画面必须符合当前时间、日程和人格,不要把身份设定里没有的场景、职业、服装或外观细节写进去。日程是背景参考，不可单独当作动作已经发生的证明。\n"
                        "2. 图片不要总是天气或窗外。先从“内容选择菜单”里单选一个视觉锚点；当前日程、话题和人格只用于筛选主体和调整画面气质,不要把多个主体拼在一张图里。若本次来自延后候选，画面应与原话题连续，不应伪装成发送当下的新现场。\n"
                        "3. 可以是路上风景、桌面小物、随手自拍、偶遇小动物等,但不要每次都是自拍；没有明确自拍动机时优先 text2img。\n"
                        "4. `prompt` 必须使用英文，并严格遵守“提示词表达方式”；可以把必要中文专名作为 visual note 保留，但不要写任务说明或聊天口吻。\n"
                        "5. `prompt` 里要明确体现上面的风格要求。\n"
                        "6. 不要包含 NSFW、隐私信息、用户真实电脑画面。\n"
                        "7. 如果“话题”已经很具体,就优先把那个具体视觉主体画出来；如果话题很抽象,从菜单里另选一个适合拍照的具体画面。不要退回成泛泛的天气图、手部动作或普通记录照。\n"
                        "8. 不要默认生成全身镜/对镜自拍/手机挡脸自拍；只有话题、动机或当前日程明确出现“镜前/对镜/镜子/全身镜/mirror”时才允许。普通穿搭图用当前地点里的手持自拍、半身或四分之三身环境人像。\n"
                        "9. `use_persona_reference` 仅表示画面中是否出现 Bot 本人：自拍、人物生活照、人物穿搭图填 true；纯风景、食物、桌面物品、动物、手机屏幕或生日卡填 false。\n"
                        "10. 服装语义优先级为：本次明确服装需求优先；具体场景服装参考用于落实该需求；今日穿搭仅在没有新服装意图时作为连续性补充。不要同时写入彼此冲突的两套服装。\n"
                        "11. 只有当前请求明确要求关系角色出现/合影，且选中了对应的角色参考图时，才可让该角色按参考图自然入镜；否则禁止凭文字补画另一人的脸、身体、背影、剪影、倒影或肖像。未明确要求时，关系卡只影响情境，并用非人物生活线索间接表达关系。"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            )
        )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "photo_prompt"},
        )

    async def _build_photo_scene_prompt(
        self, user: dict[str, Any], name: str, reason: str
    ) -> dict[str, Any]:
        prompt_format = self._photo_generation_prompt_format_mode()
        prompt_format_instruction = self._photo_generation_prompt_format_instruction()
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt_en = self._photo_style_prompt_en(style_name, style_instruction)
        topic_hint = _single_line(user.get("planned_proactive_topic"), 60)
        motive_hint = _single_line(user.get("planned_proactive_motive"), 120)
        schedule_context = self._format_plan_item_for_prompt(current_item)
        pure_scene_context = "；".join(
            part for part in (topic_hint, motive_hint, schedule_context) if part
        )
        explicit_person_scene = bool(
            re.search(
                r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                r"少女|少年|路人|猫女|拟人|"
                r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit)\b",
                pure_scene_context,
                flags=re.I,
            )
        )
        explicit_pure_scene = reason == "birthday_celebration" or (
            bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    pure_scene_context,
                    flags=re.I,
                )
            )
            and not explicit_person_scene
        )

        def scene_subject_flags(text: Any) -> tuple[bool, bool]:
            """Classify explicit person and pure-scene cues in scene text."""
            value = _single_line(text, 900)
            has_person = bool(
                re.search(
                    r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                    r"少女|少年|路人|猫女|拟人|"
                    r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit|human)\b",
                    value,
                    flags=re.I,
                )
            )
            has_scene = bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|soup|ramen|noodles|meal|bread|"
                    r"toast|sandwich|rice|fruit|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    value,
                    flags=re.I,
                )
            )
            return has_person, has_scene
        delayed_scene = bool(self._deferred_immediate_share_tense_hint(user, "photo_text"))
        if delayed_scene:
            schedule_context = "本次画面对应较早的生活片段；日程只用于保持人物与场景连续，不可作为发送当下的事实依据。"
        scene_snapshot: dict[str, Any] = {}
        scene_context = ""
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_snapshot = snapshot_builder(user)
                scene_context = _single_line(
                    snapshot_formatter(
                        scene_snapshot,
                        purpose="proactive_photo",
                    ),
                    1200,
                )
                snapshot_schedule = scene_snapshot.get("schedule")
                if not delayed_scene and isinstance(snapshot_schedule, dict):
                    schedule_context = (
                        _single_line(snapshot_schedule.get("text"), 320)
                        or schedule_context
                    )
            except Exception as exc:
                scene_snapshot = {}
                scene_context = ""
                logger.debug(
                    "主动照片读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        if not scene_context:
            scene_context = _single_line(
                "；".join(
                    part
                    for part in (
                        self._format_state_for_prompt(state if isinstance(state, dict) else {}),
                        schedule_context,
                    )
                    if part
                ),
                1200,
            )
        relationship_section: PromptSection | None = None
        if runtime_persona_setting(self, "enable_bot_relationship_network", False):
            card_lines: list[str] = []
            for raw_card in self._normalize_bot_relationship_cards(
                runtime_persona_setting(self, "bot_relationship_cards", [])
            ):
                parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
                relation = parts[1] if len(parts) > 1 else ""
                appearance = parts[2] if len(parts) > 2 else ""
                card_lines.append(f"- 角色：{parts[0]}；与Bot的关系：{relation or '（未填写）'}；外貌描述：{appearance or '（未填写）'}")
            if card_lines:
                relationship_section = prompt_section(
                    key="background.photo_scene.relationships",
                    title="Bot 关系网",
                    source="proactive_message",
                    content=(
                        "\n".join(card_lines)
                        + "\n使用方式：这些角色卡首先用于理解关系情境；角色卡文字不能替代人物参考图。只有当前请求明确点名角色/关系，或明确要求合影、合照、一起入镜时，"
                        "并且候选中确实选中了对应的角色参考图，才可让该角色按参考图自然入镜；没有匹配参考图时不要凭文字补画脸、身体、背影、剪影或倒影。"
                        "未明确要求角色出现时，仍不得让关系卡人物本人入镜，保持 Bot 单人或纯场景；在没有其他可验证人物参考时，禁止合影、合照、双人/多人同框。"
                        "可用第二只杯子、礼物、便签、空座位等非人物线索间接表达；不合适时忽略本节。"
                    ),
                )
        prompt = render_prompt_document(
            self._photo_scene_generation_prompt_document(
                persona=persona,
                recipient_name=name,
                scene_context=scene_context,
                topic_hint=topic_hint,
                motive_hint=motive_hint,
                relationship_section=relationship_section,
                birthday_rule=(
                    "如果主动原因是 birthday_celebration：制作一张没有文字、没有姓名、没有日期的温柔生日小卡。"
                    "只选一个与人格和用户偏好相称的具体意象，不画蛋糕上文字、不出现年龄、不要节庆海报或营销风。"
                    if reason == "birthday_celebration"
                    else "（非生日卡）"
                ),
                content_options=self._format_content_choice_options_for_prompt("photo_text"),
                style_name=style_name,
                style_instruction=style_instruction,
                prompt_format_instruction=prompt_format_instruction,
                reason=reason,
            )
        )["user"]
        text = ""
        try:
            text = await self._llm_call(
                prompt,
                max_tokens=260,
                provider_id=self._task_provider(
                    _persona_provider_id(self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"),
                    _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                ),
                task="photo_prompt",
            )
        except Exception as exc:
            logger.debug(
                "proactive photo prompt model failed; using deterministic fallback: %s",
                _single_line(exc, 160),
            )
        payload = self._extract_json_payload(text or "")
        model_scene_valid = isinstance(payload, dict) and bool(
            _single_line(payload.get("prompt"), 600)
        )
        if model_scene_valid:
            kind = _single_line(payload.get("kind"), 20).lower()
            image_prompt = _single_line(payload.get("prompt"), 600)
            caption = _single_line(payload.get("caption"), 180)
            raw_use_reference = payload.get("use_persona_reference")
            if isinstance(raw_use_reference, bool):
                use_persona_reference = raw_use_reference
            elif str(raw_use_reference or "").strip().lower() in {"true", "1", "yes", "是", "使用"}:
                use_persona_reference = True
            elif str(raw_use_reference or "").strip().lower() in {"false", "0", "no", "否", "不使用"}:
                use_persona_reference = False
            else:
                use_persona_reference = not explicit_pure_scene
        else:
            kind = "text2img"
            image_prompt = ""
            caption = ""
            # When the scene model is unavailable, prefer a stable character photo
            # for ordinary proactive sharing instead of allowing an arbitrary face.
            use_persona_reference = not explicit_pure_scene
        model_person_scene, model_pure_scene = scene_subject_flags(
            f"{image_prompt} {caption}"
        )
        if model_pure_scene and not model_person_scene:
            explicit_pure_scene = True
            raw_model_reference = payload.get("use_persona_reference") if isinstance(payload, dict) else None
            explicit_model_reference = (
                isinstance(raw_model_reference, bool)
                or str(raw_model_reference or "").strip().lower()
                in {"true", "1", "yes", "是", "使用", "false", "0", "no", "否", "不使用"}
            )
            if not explicit_model_reference:
                use_persona_reference = False
        if kind not in {"selfie", "portrait", "自拍", "人像", "text2img", "scene", "photo", "风景"}:
            kind = "text2img"
        if kind in {"portrait", "自拍", "人像"}:
            kind = "selfie"
        if kind in {"scene", "photo", "风景"}:
            kind = "text2img"
        if kind == "selfie":
            use_persona_reference = True
        elif isinstance(payload, dict) and payload.get("use_persona_reference") is None:
            use_persona_reference = not explicit_pure_scene
        if not image_prompt:
            if topic_hint:
                image_prompt = (
                    f"Visual note: {topic_hint}; concrete visual subject kept faithful to this topic, "
                    f"natural everyday snapshot shared with a close friend, "
                    f"the moment is motivated by {motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
            else:
                image_prompt = (
                    f"Casual everyday snapshot with a concrete subject from the current context: "
                    f"{schedule_context or 'an ordinary daily moment'}, "
                    f"{motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
        if kind == "selfie":
            mirror_context = "；".join(
                part
                for part in (
                    f"reason={reason}",
                    f"topic={topic_hint}",
                    f"motive={motive_hint}",
                    f"schedule={schedule_context}",
                )
                if _single_line(part, 260)
            )
            image_prompt = self._sanitize_unrequested_mirror_selfie_prompt(
                image_prompt,
                context_text=mirror_context,
                limit=900,
            )
        if not caption:
            if topic_hint:
                caption = f"我把{topic_hint}这个小画面拍下来分享给你。"
            elif motive_hint:
                caption = f"刚好想把这个片刻拍下来给你看看：{motive_hint}。"
            else:
                caption = "今天看到一个很适合拍下来分享的小画面。"
        if use_persona_reference:
            subject_owner = "bot"
        else:
            character_text = f"{image_prompt} {caption}"
            subject_owner = (
                "third_party"
                if re.search(r"\b(?:person|people|man|woman|boy|girl|character|human)\b", character_text, flags=re.I)
                or any(token in character_text for token in ("人物", "男人", "女人", "男生", "女生", "男孩", "女孩", "路人"))
                else "scene"
            )
        if not use_persona_reference and subject_owner == "scene":
            image_prompt = _single_line(
                f"{image_prompt}; do not insert an unrequested person, character, visible photographer, "
                "back-view figure, face, body, silhouette, or reflection",
                900,
            )
        return {
            "kind": kind,
            "prompt": image_prompt,
            "caption": caption,
            "use_persona_reference": use_persona_reference,
            "subject_owner": subject_owner,
            "scene_context": scene_context,
            "prompt_format": prompt_format,
        }

    def _get_photo_style_instruction(self) -> tuple[str, str]:
        style = str(runtime_persona_setting(self, "photo_generation_style", "真实") or "真实").strip()
        if style == "二次元":
            return "二次元", "日系二次元插画风,人物与场景干净细腻,保留生活感,不要写实摄影质感"
        if style == "其他":
            custom = _single_line(
                runtime_persona_setting(self, "photo_generation_style_custom_prompt", ""),
                200,
            )
            if custom:
                return "其他", custom
            return "其他", "保持统一审美风格,自然生活感,避免默认写实照片风格"
        return "真实", "真实摄影风格,像手机随手拍到的生活照片,光线自然,细节可信"
