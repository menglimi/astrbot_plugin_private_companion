# -*- coding: utf-8 -*-
"""IntegrationStatusPart03Mixin。

由 tools/split_mixin_domain.py 从 integration_status.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 389 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 IntegrationStatusMixin）。
"""
from __future__ import annotations

from .integration_status_shared import _ALMANAC_JI, _ALMANAC_YI, _LUNAR_DAY_NAMES, _LUNAR_MONTH_NAMES, _SOLAR_TERM_DATES
from .integration_status_shared import Any
from .integration_status_shared import AstrMessageEvent
from .integration_status_shared import Converter
from .integration_status_shared import PromptRenderMode
from .integration_status_shared import PromptSection
from .integration_status_shared import Solar
from .integration_status_shared import _single_line
from .integration_status_shared import calendar_cn
from .integration_status_shared import datetime
from .integration_status_shared import prompt_section
from .integration_status_shared import render_prompt_sections
from .integration_status_shared import runtime_persona_setting
from .integration_status_shared import timedelta
from .integration_status_shared import zoneinfo



class IntegrationStatusPart03Mixin:
    """IntegrationStatusPart03Mixin（从 IntegrationStatusMixin 拆出）。"""


    def _current_event_chat_provider_id(self, event: AstrMessageEvent) -> tuple[str, Any | None]:
        provider = None
        provider_id = ""
        try:
            provider_id = _single_line(event.get_extra("selected_provider"), 160)
        except Exception:
            provider_id = ""
        getter = getattr(self.context, "get_provider_by_id", None)
        if provider_id and callable(getter):
            try:
                provider = getter(provider_id)
            except Exception:
                provider = None
        if provider is None:
            get_using = getattr(self.context, "get_using_provider", None)
            if callable(get_using):
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                try:
                    provider = get_using(umo=umo)
                except TypeError:
                    provider = None
                except Exception:
                    provider = None
                for args in ((umo,), ()):
                    if provider is not None:
                        break
                    try:
                        provider = get_using(*args)
                        break
                    except TypeError:
                        continue
                    except Exception:
                        provider = None
                        break
        if provider is not None and not provider_id:
            provider_id = self._provider_config_value(provider, "id", "provider_id") or _single_line(getattr(provider, "provider_id", ""), 160)
        return provider_id, provider

    def _format_model_perception(self, event: AstrMessageEvent) -> str:
        if not runtime_persona_setting(self, 'enable_model_perception', True):
            return ""
        lines: list[str] = []
        chat_provider_id, chat_provider = self._current_event_chat_provider_id(event)
        lines.append(f"对话模型={self._provider_model_label(chat_provider_id, chat_provider)}")
        vision_id, vision_source, _ = self._private_image_caption_provider_id(str(getattr(event, "unified_msg_origin", "") or ""))
        if vision_id:
            source_label = {
                "astrbot_image_caption": "首选识图模型",
                "plugin_vision": "备选识图模型",
                "plugin_vision_fallback": "备选识图模型兜底",
            }.get(vision_source, vision_source or "视觉转述模型")
            lines.append(f"视觉转述模型={source_label} / {self._provider_model_label(vision_id)}")
        photo_generation = self._format_photo_generation_perception()
        if photo_generation:
            lines.append(f"生图能力={photo_generation}")
        tts_perception = self._format_tts_model_perception(event)
        if tts_perception:
            lines.append(f"TTS能力={tts_perception}")
        return "；".join(lines)

    def _format_tts_model_perception(self, event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        config: dict[str, Any] = {}
        get_config = getattr(self.context, "get_config", None)
        if callable(get_config):
            try:
                config = get_config(umo) if umo else get_config()
                if not isinstance(config, dict):
                    config = {}
            except Exception:
                config = {}
        raw_settings = config.get("provider_tts_settings", {}) if isinstance(config, dict) else {}
        provider_settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
        provider = None
        provider_getter = getattr(self.context, "get_using_tts_provider", None)
        if callable(provider_getter):
            for args in ((umo,), ()):
                try:
                    provider = provider_getter(*args)
                    break
                except TypeError:
                    continue
                except Exception:
                    provider = None
                    break
        synthesis_resolver = getattr(self, "_resolve_tts_synthesis_provider", None)
        if callable(synthesis_resolver):
            try:
                provider = synthesis_resolver(event, provider)
            except Exception:
                pass

        enhancement_enabled = bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
        if provider is None:
            availability = "已配置但当前会话不可用" if bool(provider_settings.get("enable", False)) else "当前会话未配置 TTS Provider"
        else:
            provider_id = (
                self._provider_config_value(provider, "id", "provider_id")
                or _single_line(getattr(provider, "provider_id", ""), 120)
            )
            provider_label = self._provider_identity_label(provider_id, provider)
            if provider_label == "AstrBot 默认会话模型":
                provider_label = _single_line(provider.__class__.__name__, 80) or "可用 TTS Provider"
            kind_getter = getattr(self, "_tts_provider_kind", None)
            try:
                provider_kind = kind_getter(provider, provider_settings) if callable(kind_getter) else ""
            except Exception:
                provider_kind = ""
            kind_label = {
                "fishaudio": "FishAudio",
                "gsv": "GPT-SoVITS",
                "openai": "OpenAI TTS",
                "edge": "Edge TTS",
                "azure": "Azure TTS",
                "gemini": "Gemini TTS",
                "minimax": "MiniMax TTS",
                "mimo_tts": "MiMo TTS",
                "aliyun": "阿里云 TTS",
                "volcengine": "火山引擎 TTS",
            }.get(provider_kind, "")
            if kind_label and kind_label.lower() not in provider_label.lower():
                provider_label = f"{kind_label} / {provider_label}"
            availability = f"合成 Provider 可用 / {provider_label}"

        parts = [availability, f"TTS强化:{'开启' if enhancement_enabled else '关闭'}"]
        if enhancement_enabled:
            mode_label = {
                "fast_tag": "快速标签",
                "postprocess": "后处理",
            }.get(str(runtime_persona_setting(self, 'tts_generation_mode', "fast_tag") or "fast_tag"), "快速标签")
            scope_label = "全量转换" if str(runtime_persona_setting(self, 'tts_conversion_scope', "partial") or "partial") == "full" else "局部转换"
            delivery_label = "仅语音" if str(runtime_persona_setting(self, 'tts_delivery_mode', "voice_and_text") or "voice_and_text") == "voice_only" else "语音+文字"
            language_getter = getattr(self, "_tts_language_label", None)
            language_label = language_getter() if callable(language_getter) else {
                "ja": "日语",
                "zh": "中文",
                "en": "英语",
            }.get(str(runtime_persona_setting(self, 'tts_voice_language', "zh") or "zh"), "中文")
            conversion_id = _single_line(runtime_persona_setting(self, 'tts_conversion_provider_id', ""), 120)
            if conversion_id:
                conversion_label = self._provider_model_label(conversion_id)
            else:
                conversion_label = "跟随当前对话模型"
            parts.extend(
                [
                    f"文本转换模型:{conversion_label}",
                    f"路径:{mode_label}",
                    f"语种:{language_label}",
                    f"范围:{scope_label}",
                    f"交付:{delivery_label}",
                ]
            )
        return " / ".join(parts)

    def _format_photo_generation_perception(self) -> str:
        if not (
            bool(runtime_persona_setting(self, 'enable_photo_text_action', False))
            or bool(runtime_persona_setting(self, 'enable_natural_language_photo_generation', False))
        ):
            return ""
        preferred = _single_line(runtime_persona_setting(self, 'photo_generation_backend', ""), 30) or "auto"
        external_model = _single_line(getattr(self, "external_image_api_model", ""), 80)
        configured_endpoints = getattr(self, "external_image_api_endpoints", [])
        endpoint_queue: list[dict[str, Any]] = []
        if isinstance(configured_endpoints, list) and configured_endpoints:
            queue_getter = getattr(self, "_external_image_api_endpoint_queue", None)
            if callable(queue_getter):
                try:
                    endpoint_queue = [
                        endpoint
                        for endpoint in queue_getter(include_incomplete=True, include_disabled=True)
                        if isinstance(endpoint, dict)
                    ]
                except Exception:
                    endpoint_queue = []
        platform = "openai"
        resolver = getattr(self, "_resolved_external_image_api_platform", None)
        if callable(resolver):
            try:
                platform = str(resolver() or "openai")
            except Exception:
                platform = "openai"
        comfyui_workflow = _single_line(getattr(self, "comfyui_text2img_workflow_name", ""), 60)
        selfie_workflow = _single_line(getattr(self, "comfyui_selfie_workflow_name", ""), 60)
        comfyui_available = bool(getattr(self, "_comfyui_photo_available", lambda: False)())
        sdgen_available = bool(getattr(self, "_sdgen_photo_available", lambda: False)())
        external_available = bool(getattr(self, "_external_photo_available", lambda: False)())
        tool_call_available = bool(getattr(self, "_custom_tool_photo_available", lambda: False)())
        tool_call_name = _single_line(getattr(self, "custom_photo_tool_name", ""), 80)

        def comfyui_label() -> str:
            labels = []
            if comfyui_workflow:
                labels.append(f"文生图:{comfyui_workflow}")
            if selfie_workflow and selfie_workflow != comfyui_workflow:
                labels.append(f"自拍:{selfie_workflow}")
            suffix = f" / {', '.join(labels)}" if labels else ""
            return f"ComfyUI{suffix}"

        def external_label() -> str:
            if endpoint_queue:
                ready_count = 0
                note_getter = getattr(self, "_external_image_api_endpoint_unavailable_note", None)
                for endpoint in endpoint_queue:
                    if callable(note_getter):
                        try:
                            if not note_getter(endpoint):
                                ready_count += 1
                        except Exception:
                            pass
                first = endpoint_queue[0] if endpoint_queue else {}
                first_model = _single_line(first.get("model"), 60) if isinstance(first, dict) else ""
                return f"在线图片 API 队列 {ready_count}/{len(endpoint_queue)} 可用 / 优先 {first_model or '未填模型'}"
            prefix = (
                "阿里云百炼"
                if platform == "bailian"
                else "MiniMax"
                if platform == "minimax"
                else "在线图片 API"
            )
            return f"{prefix} / {external_model or '未填模型'}"

        if preferred == "nai":
            nai_available = bool(getattr(self, "_nai_image_available", lambda: False)())
            return "NAI 生图（直连）" if nai_available else "NAI 生图直连（未检测到 NAI 生图插件）"
        if preferred == "external":
            return external_label()
        if preferred == "comfyui":
            return comfyui_label()
        if preferred == "sdgen":
            return "SDGen"
        if preferred == "anima_master":
            available = bool(self._image_companion_backend_available("anima_master"))
            return "Anima 绘图大师（直连）" if available else "Anima 绘图大师（未检测到兼容插件）"
        if preferred == "tool_call":
            return f"函数工具 / {tool_call_name or '未配置'}" if tool_call_available else f"函数工具（未找到 {tool_call_name or '未配置'}）"
        if external_available:
            return f"auto -> {external_label()}"
        if comfyui_available:
            return f"auto -> {comfyui_label()}"
        if sdgen_available:
            return "auto -> SDGen"
        if external_model:
            return f"auto（候选：{external_label()}）"
        return "auto（当前无可用生图后端）"

    async def _format_environment_perception_body(
        self,
        event: AstrMessageEvent,
    ) -> str:
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_environment_perception"):
                return ""
        elif not runtime_persona_setting(self, 'enable_environment_perception', True):
            return ""
        current = self._environment_now()
        lines = [
            "这是当前消息的背景边界，主要影响语境判断、节奏和措辞；如果用户明确问到时间、节日、平台或环境线索，可以按需要自然回答，没问到时就把它当作背景参考。",
        ]
        holiday = self._format_holiday_perception(current)
        if holiday:
            lines.append(f"时间：{current.strftime('%Y-%m-%d %H:%M')}（{holiday}）")
        else:
            lines.append(f"时间：{current.strftime('%Y-%m-%d %H:%M')}")
        season_parts = []
        lunar = self._format_lunar_perception(current)
        if lunar:
            season_parts.append(f"农历{lunar}")
        solar_term = self._format_solar_term_perception(current)
        if solar_term:
            season_parts.append(f"节气{solar_term}")
        almanac = self._format_almanac_perception(current)
        if almanac:
            season_parts.append(almanac)
        if season_parts:
            lines.append(f"时令：{'；'.join(season_parts)}")
        platform = await self._format_platform_perception(event)
        if platform:
            lines.append(f"会话：{platform}")
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private_chat = False
        if not is_private_chat:
            try:
                sender_id = _single_line(str(event.get_sender_id()), 40)
            except Exception:
                sender_id = ""
            sender_name = ""
            try:
                sender_name = _single_line(self._sender_display_name(event), 40)
            except Exception:
                sender_name = ""
            if sender_id:
                label = f"{sender_name}[QQ:{sender_id}]" if sender_name and sender_name != sender_id else f"QQ:{sender_id}"
                lines.append(
                    "群聊身份边界：本轮当前发言者是"
                    f"{label}；环境感知只提供当前消息背景，不能把上一位说话人的专属关系身份继承给当前发言者；"
                    "该 ID 只供内部判断，不要在回复正文里复述。"
                )
        model = self._format_model_perception(event)
        if model:
            lines.append(f"模型：{model}")
        return "\n".join(lines)

    async def _format_environment_perception(
        self,
        event: AstrMessageEvent,
    ) -> str:
        section = await self._format_environment_perception_prompt_section(event)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    async def _format_environment_perception_prompt_section(
        self,
        event: AstrMessageEvent,
    ) -> PromptSection:
        return prompt_section(
            key="environment.perception",
            title="环境感知",
            source="integration_status",
            content=await self._format_environment_perception_body(event),
        )

    def _environment_timezone(self) -> zoneinfo.ZoneInfo | None:
        timezone_name = _single_line(self.environment_perception_timezone, 64) or "Asia/Shanghai"
        try:
            return zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            return None

    def _environment_now(self) -> datetime:
        tz = self._environment_timezone()
        return datetime.now(tz) if tz is not None else datetime.now()

    def _environment_fromtimestamp(self, timestamp: float) -> datetime:
        tz = self._environment_timezone()
        return datetime.fromtimestamp(timestamp, tz) if tz is not None else datetime.fromtimestamp(timestamp)

    def _environment_today_key(self) -> str:
        return self._environment_now().strftime("%Y-%m-%d")

    def _environment_now_minutes(self) -> int:
        current = self._environment_now()
        return current.hour * 60 + current.minute

    def _format_holiday_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_holiday_perception', True):
            return ""
        label, _ = self._current_time_period_label(current)
        weekday = "一二三四五六日"[current.weekday()]
        parts = [f"周{weekday}"]
        if runtime_persona_setting(self, 'holiday_country', 'CN') != "CN" or calendar_cn is None:
            parts.append("周末" if current.weekday() >= 5 else "工作日")
        else:
            try:
                if calendar_cn.is_holiday(current.date()):
                    name = _single_line(calendar_cn.get_holiday_detail(current.date())[1], 30)
                    parts.append(name or "节假日")
                elif calendar_cn.is_workday(current.date()):
                    parts.append("工作日")
                else:
                    parts.append("休息日")
            except Exception:
                parts.append("周末" if current.weekday() >= 5 else "工作日")
        parts.append(label)
        return "、".join(part for part in parts if part)

    def _format_lunar_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_lunar_perception', True) or Converter is None or Solar is None:
            return ""
        try:
            lunar = Converter.Solar2Lunar(Solar(current.year, current.month, current.day))
            month_index = max(1, min(12, int(getattr(lunar, "month", 1)))) - 1
            day_index = max(1, min(30, int(getattr(lunar, "day", 1)))) - 1
            leap = "闰" if bool(getattr(lunar, "isleap", False)) else ""
            return f"{leap}{_LUNAR_MONTH_NAMES[month_index]}{_LUNAR_DAY_NAMES[day_index]}"
        except Exception:
            return ""

    def _format_solar_term_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_solar_term_perception', True):
            return ""
        today = (current.month, current.day)
        if today in _SOLAR_TERM_DATES:
            return _SOLAR_TERM_DATES[today]
        current_date = current.date()
        for offset in range(1, 4):
            next_day = current_date + timedelta(days=offset)
            name = _SOLAR_TERM_DATES.get((next_day.month, next_day.day))
            if name:
                return f"{offset}天后{name}"
        return ""

    def _format_almanac_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_almanac_perception', False):
            return ""
        seed = current.year * 10000 + current.month * 100 + current.day
        yi = _ALMANAC_YI[seed % len(_ALMANAC_YI)]
        ji = _ALMANAC_JI[(seed // 7) % len(_ALMANAC_JI)]
        return f"宜{yi}，忌{ji}"
