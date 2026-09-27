# -*- coding: utf-8 -*-
"""每日穿搭提示词分段与风格提示域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 392 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

from .conversation_prompt_section import PhotoPromptContent, PromptSection, prompt_section
from .helpers import _safe_int, _single_line
from .persona_config import runtime_persona_setting
from typing import Any



class ProactiveMessagePhotoGenerationDailyOutfitPromptHintsMixin:
    """每日穿搭提示词分段与风格提示域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _build_daily_outfit_photo_prompt_sections(
        self,
        diary: dict[str, Any],
        *,
        memory_context: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> tuple[PromptSection, ...]:
        persona = self._daily_outfit_role_appearance_text()
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt = self._photo_style_prompt_en(style_name, style_instruction)
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        schedule_hint = self._daily_outfit_schedule_text()
        state_visual = self._daily_outfit_visual_state_text(state if isinstance(state, dict) else {})
        outfit_profile = self._normalize_daily_outfit_profile(outfit_profile)
        if not outfit_profile:
            outfit_profile = self._select_daily_outfit_profile(schedule_hint=schedule_hint, weather=weather)
        outfit_hint = self._daily_outfit_outfit_hint(
            schedule_hint=schedule_hint,
            weather=weather,
            outfit_profile=outfit_profile,
        )
        rotation_reference = self._daily_outfit_rotation_reference()
        scene_hint = self._daily_outfit_scene_hint(state if isinstance(state, dict) else {}, schedule_hint=schedule_hint, weather=weather)
        visual_memory = ""
        visual_memory_getter = getattr(self, "_visual_photo_memory_context", None)
        if callable(visual_memory_getter):
            try:
                visual_memory = visual_memory_getter(memory_context, limit=260)
            except Exception:
                visual_memory = ""
        diary_hint = _single_line(
            (diary or {}).get("summary")
            or (diary or {}).get("share_seed")
            or (diary or {}).get("body"),
            80,
        )
        custom = _single_line(
            runtime_persona_setting(self, "daily_outfit_photo_prompt", ""), 220
        )
        anime_style = style_name == "二次元"
        composition_style = (
            [
                "daily outfit character illustration",
                "selfie-inspired outfit portrait composition",
                "non-mirror casual illustrated portrait",
                "soft illustrated lighting",
                "clean illustrated background",
                "anime slice-of-life atmosphere",
            ]
            if anime_style
            else [
                "daily outfit selfie",
                "selfie outfit photo",
                "non-mirror handheld selfie or natural environmental outfit portrait",
                "natural phone snapshot",
                "soft natural light",
                "clean background",
                "lifelike daily atmosphere",
            ]
        )
        positive = [
            "single character",
            *composition_style[:3],
            "solo",
            "visible face",
            "complete head and hair",
            "clear eyes",
            "natural expression",
            "upper body to three-quarter body portrait, not a full-length mirror shot",
            "centered composition",
            "1:1 square cover composition",
            "safe margins around head and body",
            *composition_style[3:],
            persona or "keep the face, hairstyle, hair color, eye color, and key traits consistent with the reference image",
            outfit_hint,
            scene_hint,
            state_visual or "relaxed natural mood",
            style_prompt,
        ]
        if rotation_reference:
            positive.append(
                "wardrobe rotation: show exactly one character wearing one coherent new outfit in this single image; "
                "make that one outfit differ from recent daily outfit photos in at least two design dimensions, "
                f"but never display the old outfit or multiple alternatives; avoid repeating {rotation_reference}"
            )
        if diary_hint:
            positive.append(f"daily mood cue: {diary_hint}")
        negative = [
            "cropped head",
            "headless",
            "faceless",
            "face hidden",
            "extreme close-up",
            "arm in foreground",
            "body only",
            "outfit only",
            "back view",
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "standing in front of a mirror",
            "dressing room mirror",
            "phone covering face",
            "cut off face",
            "bad hands",
            "extra fingers",
            "text",
            "caption",
            "label",
            "watermark",
            "logo",
            "other people",
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
            "user in frame",
            "private screen",
            "nsfw",
            "revealing outfit",
        ]
        if anime_style:
            negative.extend(
                [
                    "photorealistic",
                    "real person",
                    "live-action",
                    "realistic photography",
                    "photo-real skin texture",
                ]
            )
        if rotation_reference:
            negative.extend(
                [
                    "same outfit as a recent daily outfit photo",
                    f"repeat any recently used outfit element: {rotation_reference}",
                ]
            )
        sections = [
            prompt_section(
                key="photo.daily_outfit.user_request",
                title="user_request",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    positive=_single_line(
                        ", ".join(
                            _single_line(part, 400)
                            for part in positive
                            if _single_line(part, 400)
                        ),
                        1400,
                    ),
                    domain_source="user_request",
                    protected=True,
                ),
            ),
            prompt_section(
                key="photo.daily_outfit.contract",
                title="daily_outfit_contract",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    # These are resolved workflow exclusions rather than ambient
                    # visual context. Freeze them for this task so the N-1 resolver
                    # preserves safety and wardrobe-rotation rules.
                    negative=_single_line(", ".join(negative), 760),
                    domain_source="fixed_prompt",
                    protected=True,
                ),
            ),
        ]
        if visual_memory:
            sections.append(
                prompt_section(
                    key="photo.daily_outfit.visual_memory",
                    title="visual_memory",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"visual continuity reference: {visual_memory}",
                        domain_source="visual_memory",
                    ),
                )
            )
        if custom:
            sections.append(
                prompt_section(
                    key="photo.daily_outfit.preference",
                    title="daily_outfit_preference",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"additional outfit preference: {custom}",
                        domain_source="fixed_prompt",
                    ),
                )
            )
        return tuple(sections)

    def _photo_style_prompt_en(self, style_name: str, style_instruction: str = "") -> str:
        name = _single_line(style_name, 40)
        instruction = _single_line(style_instruction, 220)
        if name == "二次元":
            return "2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling"
        if name == "真实":
            return "realistic photography style, believable phone photo, natural lighting, realistic fabric details"
        if instruction:
            return instruction
        return "consistent visual style, natural daily-life feeling"

    def _daily_outfit_visual_state_text(self, state: dict[str, Any]) -> str:
        fragments: list[str] = []
        energy = _safe_int((state or {}).get("energy"), 70, 0, 100)
        if energy < 40:
            fragments.append("slightly sleepy, soft expression")
        elif energy > 82:
            fragments.append("fresh and energetic, bright eyes")
        mood = _single_line((state or {}).get("mood_bias"), 20).replace("黏人", "粘人")
        mood_map = {
            "开心": "gentle happy mood",
            "轻快": "light cheerful mood",
            "柔和": "soft gentle mood",
            "安静": "quiet calm mood",
            "疲惫": "tired but gentle mood",
            "困": "sleepy mood",
            "困倦": "sleepy mood",
            "低落": "subdued mood",
            "敏感": "delicate sensitive mood",
            "粘人": "soft attached mood",
        }
        if mood and mood not in {"平稳", "中性"}:
            fragments.append(mood_map.get(mood, f"{mood} mood"))
        conditions = (state or {}).get("conditions")
        visual_tokens = ("雨", "风", "冷", "热", "困", "疲", "生理期", "感冒", "发烧", "头痛", "胃", "睡", "醒")
        if isinstance(conditions, list):
            for cond in conditions[:6]:
                if not isinstance(cond, dict):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 30)
                if label and any(token in label for token in visual_tokens):
                    fragments.append(self._daily_outfit_condition_hint_en(label))
                if len(fragments) >= 3:
                    break
        return _single_line(", ".join(dict.fromkeys(item for item in fragments if item)), 140)

    def _daily_outfit_condition_hint_en(self, text: str) -> str:
        value = _single_line(text, 60).lower()
        if any(token in value for token in ("雨", "rain", "淋")):
            return "rainy-day softness"
        if any(token in value for token in ("风", "wind")):
            return "slight wind-blown hair"
        if any(token in value for token in ("冷", "寒", "snow")):
            return "cold-weather outfit"
        if any(token in value for token in ("热", "暑", "hot")):
            return "light breathable outfit"
        if any(token in value for token in ("困", "疲", "睡", "醒")):
            return "sleepy gentle expression"
        if any(token in value for token in ("生理期", "胃", "感冒", "发烧", "头痛")):
            return "soft low-energy expression"
        return _single_line(text, 60)

    def _daily_outfit_scene_hint(self, state: dict[str, Any], *, schedule_hint: str = "", weather: str = "") -> str:
        location = ""
        try:
            location = _single_line(self._current_location_state_text(state), 60)
            coarse = _single_line(self._coarse_roleplay_location_text(location), 40)
            location = coarse or location
        except Exception:
            location = ""
        text = f"{schedule_hint} {weather}".lower()
        if not location:
            if any(token in text for token in ("上课", "教室", "学校", "校门", "放学", "自习")):
                location = "school or commute-to-school setting"
            elif any(token in text for token in ("出门", "路上", "街", "公交", "地铁", "下班", "回家")):
                location = "outdoor street or commute setting"
            elif any(token in text for token in ("家", "房间", "卧室", "起床", "午休", "睡")):
                location = "home or bedroom setting"
            else:
                location = "daily-life setting"
        else:
            location = self._daily_outfit_location_hint_en(location)
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        return _single_line(", ".join(part for part in [location, weather_hint, "simple background, lived-in daily atmosphere"] if part), 180)

    def _daily_outfit_location_hint_en(self, location: str) -> str:
        text = _single_line(location, 80).lower()
        if any(token in text for token in ("学校", "教室", "上课", "school", "classroom")):
            return "school or classroom setting"
        if any(token in text for token in ("家", "房间", "卧室", "home", "room", "bedroom")):
            return "home or bedroom setting"
        if any(token in text for token in ("工作", "office", "公司")):
            return "workplace or office setting"
        if any(token in text for token in ("外面", "路", "街", "通勤", "outside", "street")):
            return "outdoor street or commute setting"
        return _single_line(location, 80)

    def _daily_outfit_weather_visual_hint(self, weather: str) -> str:
        text = _single_line(weather, 200).lower()
        if not text:
            return ""
        hints: list[str] = []
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain")):
            hints.append("rainy-day atmosphere, umbrella or damp ground, light jacket")
        if any(token in text for token in ("风", "大风", "强对流", "wind")):
            hints.append("windy feeling, slightly wind-blown hair and hem")
        if any(token in text for token in ("冷", "降温", "低温", "寒", "snow")):
            hints.append("cold weather, warm outerwear")
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot")):
            hints.append("hot weather, light breathable clothes")
        return _single_line(", ".join(dict.fromkeys(hints)), 140)

    def _daily_outfit_outfit_hint(
        self,
        *,
        schedule_hint: str = "",
        weather: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> str:
        profile = self._normalize_daily_outfit_profile(outfit_profile)
        if profile:
            fields = (
                ("palette", "color palette"),
                ("silhouette", "silhouette"),
                ("top", "top"),
                ("outer", "outer layer"),
                ("bottom", "bottoms"),
                ("footwear", "footwear"),
                ("accessory", "accessories"),
            )
            hints = ["intentionally distinct coordinated daily outfit"]
            hints.extend(
                f"{label}: {profile[key]}"
                for key, label in fields
                if profile.get(key)
            )
            return _single_line(", ".join(hints), 620)
        text = f"{schedule_hint} {weather}".lower()
        hints: list[str] = []
        if any(token in text for token in ("校服", "上课", "教室", "学校", "高一", "自习", "放学")):
            hints.append("neat school outfit or school-uniform inspired outfit")
        if any(token in text for token in ("上班", "工作", "会议", "通勤")):
            hints.append("clean daily commute outfit")
        if any(token in text for token in ("运动", "跑步", "健身", "体育")):
            hints.append("light sporty outfit")
        if any(token in text for token in ("家", "房间", "午休", "整理", "起床")) and not hints:
            hints.append("soft casual home outfit")
        if any(token in text for token in ("睡衣", "睡前", "入睡", "刚醒")) and not any(token in text for token in ("上课", "上班", "出门", "通勤")):
            hints.append("comfortable pajamas or loungewear")
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        if weather_hint:
            hints.append(weather_hint)
        if not hints:
            hints.append("natural daily outfit, coordinated colors, clear clothing layers")
        return _single_line(", ".join(dict.fromkeys(hints)), 180)

    def _daily_outfit_role_appearance_text(self) -> str:
        persona = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        recognition = str(
            runtime_persona_setting(self, "private_image_self_recognition_hint", "") or ""
        )
        labels = {
            "性别": "gender",
            "识别点": "key visual traits",
            "外貌": "appearance",
            "主要识别点": "key visual traits",
            "发型发色": "hairstyle and hair color",
            "发色": "hair color",
            "发型": "hairstyle",
            "瞳色": "eye color",
            "眼睛": "eyes",
            "服饰风格": "clothing style",
            "服装": "clothing",
            "衣着": "outfit",
        }
        parts: list[str] = []
        for line in persona.replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = _single_line(value, 160)
            english_label = labels.get(label)
            if english_label and value:
                parts.append(f"{english_label}: {value}")
        if recognition:
            parts.append(f"additional visual recognition notes: {_single_line(recognition, 180)}")
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return _single_line(", ".join(unique), 620)
