# -*- coding: utf-8 -*-
"""voice_tts 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 559 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import importlib
import random
import re
from .constants import VOICE_FALLBACK_TEMPLATES
from .conversation_prompt_section import (
    PromptDocument,
    PromptLabelStyle,
    PromptRenderMode,
    prompt_document,
    prompt_section,
    render_prompt_document,
)
from .helpers import _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from astrbot.core import file_token_service
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需真实对象（isinstance/下标/继承）：复制宿主的 import 语句 ====
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
# ---- 宿主全局转发层结束 ----

class ProactiveMessageVoiceTtsMixin:
    """voice_tts 域（从 ProactiveMessageMixin 拆出）。"""


    async def _generate_voice_note_via_framework(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        target: str,
        strict_tts: bool = False,
    ) -> str:
        umo = str(user.get("umo") or target or "").strip()
        if not umo:
            return ""
        prompt = self._build_framework_voice_prompt(
            user=user,
            name=name,
            reason=reason,
            target=target,
            strict_tts=strict_tts,
        )
        try:
            raw_text = await self._run_framework_agent_text(
                umo=umo,
                prompt=prompt,
                name=name,
                label="proactive_voice",
                task="proactive_voice",
                user=user,
                max_steps=20,
            )
            return str(raw_text or "").strip()
        except Exception as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning("主动语音主链被会话数据库锁住,本轮跳过并等待下次调度: %s", _single_line(umo, 120))
            else:
                logger.warning("主动语音主链内容生成失败: %s", exc)
            return ""

    def _extract_group_id_from_umo(self, target: str) -> int | None:
        text = str(target or "").strip()
        match = re.search(r":GroupMessage:(\d+)$", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    @staticmethod
    def _voice_fallback_prompt_document(
        *,
        persona: str,
        name: str,
        relationship_level: str,
        relationship_preference: str,
        last_user_message: str,
        reason: str,
        state: str,
        tts_prompt: str,
        max_chars: int,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.voice.task",
                    title="主动语音生成",
                    source="proactive_message",
                    content="你正在替角色写一条马上要发出去的语音。这条语音是真的会被 TTS 念出来,不是文字陪聊。",
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.voice.persona",
                    title="人格",
                    source="proactive_message",
                    content=persona,
                ),
                prompt_section(
                    key="background.voice.recipient",
                    title="对象",
                    source="proactive_message",
                    content=(
                        f"称呼：{name}\n"
                        f"关系：{relationship_level}｜偏好：{relationship_preference}\n"
                        f"最近一句：{last_user_message or '（暂无）'}"
                    ),
                ),
                prompt_section(
                    key="background.voice.reason",
                    title="主动原因",
                    source="proactive_message",
                    content=reason,
                ),
                prompt_section(
                    key="background.voice.state",
                    title="当前状态",
                    source="proactive_message",
                    content=state,
                ),
                prompt_section(
                    key="background.voice.tts",
                    title="当前会话 TTS 规则",
                    source="proactive_message",
                    content=tts_prompt or "（当前没有额外 TTS 提示词,就按人格自己的语音习惯来）",
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.voice.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "1. 优先遵守人格里自己写的特殊 TTS 规则；如果人格或当前会话 TTS 规则要求使用 <tts>...</tts>、日语、情绪标签或双语格式,就按那个格式输出。\n"
                        "2. 如果没有明确格式要求,就只输出适合真正念出来的一小句语音内容,不要解释。\n"
                        f"3. 整体要短,适合私聊语音,不像朗读稿,也不要太正式；纯中文可控制在 {max_chars} 个字以内。\n"
                        "4. 可以有一点嘴硬、黏人、藏着的想念,但不要把喜欢说满。\n"
                        "5. 不要提 AI、模型、插件、TTS、语音合成这些词。"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "voice"},
        )

    async def _build_voice_note_text(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        target: str = "",
    ) -> str:
        requirement = self._voice_requirement_profile(target)
        framework_text = await self._generate_voice_note_via_framework(
            user,
            name,
            reason,
            target=target,
        )
        if framework_text:
            spoken = str(framework_text).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                logger.info(
                    "主动语音未命中格式要求,进行框架严格重试: target=%s summary=%s",
                    target,
                    requirement["summary"],
                )
                retry_text = await self._generate_voice_note_via_framework(
                    user,
                    name,
                    reason,
                    target=target,
                    strict_tts=True,
                )
                if retry_text:
                    spoken = str(retry_text).strip()
            if "<tts>" not in spoken:
                spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                repair_prompt = self._build_voice_repair_prompt(
                    spoken=spoken,
                    requirement=requirement,
                    target=target,
                )
                repaired = await self._llm_call(
                    repair_prompt,
                    max_tokens=140,
                    provider_id=self._task_provider(
                        _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                        _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                    ),
                    task="voice_repair",
                )
                if repaired:
                    spoken = str(repaired).strip()
                    if "<tts>" not in spoken:
                        spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                        spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                logger.warning(
                    "主动语音仍未完全命中格式要求,保留当前结果: target=%s summary=%s text=%s",
                    target,
                    requirement["summary"],
                    self._strip_tts_markup(spoken),
                )
            else:
                logger.info(
                    "主动语音最终文本已命中格式要求: target=%s text=%s",
                    target,
                    self._strip_tts_markup(spoken),
                )
            return spoken
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        last_user_message = _single_line(user.get("last_user_message"), 80)
        profile = self._relationship_profile(user)
        tts_prompt = self._get_tts_prompt_text(target)
        prompt = render_prompt_document(
            self._voice_fallback_prompt_document(
                persona=persona,
                name=name,
                relationship_level=profile["level"],
                relationship_preference=profile["preference"],
                last_user_message=last_user_message,
                reason=reason,
                state=self._format_state_for_prompt(state if isinstance(state, dict) else {}),
                tts_prompt=tts_prompt,
                max_chars=runtime_persona_setting(self, "voice_action_max_chars", 30),
            )
        )["user"]
        text = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=self._task_provider(
                _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="voice",
        )
        spoken = str(text or "").strip()
        if not spoken:
            spoken = random.choice(VOICE_FALLBACK_TEMPLATES)
        if "<tts>" not in spoken:
            spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
            spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
        if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
            repair_prompt = self._build_voice_repair_prompt(
                spoken=spoken,
                requirement=requirement,
                target=target,
            )
            repaired = await self._llm_call(
                repair_prompt,
                max_tokens=140,
                provider_id=self._task_provider(
                    _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                    _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                ),
                task="voice_repair",
            )
            if repaired:
                spoken = str(repaired).strip()
                if "<tts>" not in spoken:
                    spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                    spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
        return spoken

    async def _create_voice_record_component(
        self,
        target: str,
        spoken_text: str,
        *,
        defer_local_playback: bool = False,
    ) -> tuple[list[Any], str]:
        if not spoken_text:
            return [], "语音内容为空"
        try:
            config = self.context.get_config(target)
        except Exception:
            try:
                config = self.context.get_config()
            except Exception as e:
                return [], f"读取配置失败：{e}"
        provider_settings = dict(config.get("provider_tts_settings", {}) or {})
        astrbot_provider = None
        try:
            astrbot_provider = self.context.get_using_tts_provider(target)
        except Exception as e:
            logger.debug("主动语音读取 AstrBot TTS provider 失败: %s", _single_line(e, 120))
        resolver = getattr(self, "_resolve_tts_synthesis_provider", None)
        if callable(resolver):
            try:
                tts_provider = resolver(SimpleNamespace(unified_msg_origin=target), astrbot_provider)
            except Exception:
                tts_provider = astrbot_provider
        else:
            tts_provider = astrbot_provider
        if not tts_provider:
            return [], "当前没有可用的 AstrBot TTS Provider 或 MiMo Voice Clone 联动"
        if tts_provider is astrbot_provider and not provider_settings.get("enable", False):
            return [], "当前会话未启用 AstrBot TTS"
        if "<tts>" in spoken_text and "</tts>" in spoken_text:
            components, note = await self._build_tts_modify_components(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
            )
            records = [component for component in components if isinstance(component, Record)]
            if records:
                # The proactive message generator supplies the visible companion text.
                # Keep only the prebuilt audio here so it cannot be sent twice.
                return records, note
        record_builder = getattr(self, "_tts_record_component", None)
        if callable(record_builder):
            record_kwargs = {
                "source_text": spoken_text,
                "source": "private_companion",
            }
            if defer_local_playback:
                record_kwargs["defer_delivery_effects"] = True
            try:
                record = await record_builder(
                    spoken_text,
                    tts_provider,
                    provider_settings,
                    config,
                    **record_kwargs,
                )
            except TypeError:
                record_kwargs.pop("defer_delivery_effects", None)
                record = await record_builder(
                    spoken_text,
                    tts_provider,
                    provider_settings,
                    config,
                    **record_kwargs,
                )
            if record is not None:
                return [record], self._extract_record_note([record]) or "已通过 TTS强化生成语音"
            return [], "TTS 没有返回音频文件"
        try:
            audio_path = await tts_provider.get_audio(spoken_text)
        except Exception as e:
            logger.warning(f"voice 主动行为生成失败: {e}")
            return [], str(e)
        if not audio_path:
            return [], "TTS 没有返回音频文件"
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            if not audio_file.is_relative_to(expected_dir):
                return [], f"语音文件路径不安全：{audio_path}"
        except Exception as e:
            return [], str(e)
        final_ref = str(audio_path)
        if provider_settings.get("use_file_service", False):
            callback_api_base = str(config.get("callback_api_base", "") or "").strip()
            if callback_api_base:
                try:
                    token = await file_token_service.register_file(str(audio_path))
                    final_ref = f"{callback_api_base}/api/file/{token}"
                except Exception as e:
                    logger.warning(f"注册语音文件失败,将回退到本地路径: {e}")
        try:
            component = Record(file=final_ref, url=final_ref)
        except TypeError:
            try:
                component = Record(file=final_ref)
            except TypeError:
                component = Record.fromFileSystem(str(audio_path))
        self._annotate_tts_record_component(component, spoken_text, source_text=spoken_text)
        return [component], str(audio_path)

    def _get_tts_prompt_text(self, target: str) -> str:
        if runtime_persona_setting(self, "enable_tts_enhancement", False):
            builder = getattr(self, "_build_tts_rule_prompt", None)
            if callable(builder):
                return str(builder("generic") or "").strip()
        return ""

    def _voice_requirement_profile(self, target: str) -> dict[str, Any]:
        persona = self._get_default_persona_prompt()
        tts_prompt = self._get_tts_prompt_text(target)
        combined = f"{persona}\n{tts_prompt}".lower()
        require_tts_tags = "<tts>" in combined or "</tts>" in combined
        japanese_markers = ("日语", "日文", "日本語", "假名", "片假名", "平假名", "日语语音", "日文语音")
        bilingual_markers = ("双语", "中日双语", "中文文本", "中文显示", "日语语音")
        prefer_japanese = any(marker in combined for marker in japanese_markers)
        prefer_bilingual = any(marker in combined for marker in bilingual_markers)
        strict = require_tts_tags or prefer_japanese or prefer_bilingual
        parts: list[str] = []
        if require_tts_tags:
            parts.append("需要 <tts> 标签")
        if prefer_japanese:
            parts.append("语音正文优先日语")
        if prefer_bilingual:
            parts.append("可能需要双语/日中并存格式")
        if not parts:
            parts.append("没有明显额外语音格式要求")
        return {
            "strict": strict,
            "require_tts_tags": require_tts_tags,
            "prefer_japanese": prefer_japanese,
            "prefer_bilingual": prefer_bilingual,
            "summary": "；".join(parts),
        }

    def _voice_text_matches_requirement(self, spoken: str, requirement: dict[str, Any]) -> bool:
        text = str(spoken or "").strip()
        if not text:
            return False
        if requirement.get("require_tts_tags") and ("<tts>" not in text.lower() or "</tts>" not in text.lower()):
            return False
        core = self._strip_tts_markup(text)
        if requirement.get("prefer_japanese"):
            if not re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", core):
                return False
        return True

    @staticmethod
    def _voice_repair_prompt_document(
        *,
        persona: str,
        tts_prompt: str,
        requirement_summary: str,
        spoken: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.voice_repair.task",
                    title="主动语音格式修正",
                    source="proactive_message",
                    content="你要把下面这句主动语音修正成符合当前语音规则的最终版本。",
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.voice_repair.persona",
                    title="人格",
                    source="proactive_message",
                    content=persona,
                ),
                prompt_section(
                    key="background.voice_repair.tts",
                    title="当前会话 TTS 规则",
                    source="proactive_message",
                    content=tts_prompt or "（当前没有额外 TTS 提示词）",
                ),
                prompt_section(
                    key="background.voice_repair.requirement",
                    title="必须满足的格式重点",
                    source="proactive_message",
                    content=requirement_summary or "按人格自己的语音习惯处理",
                ),
                prompt_section(
                    key="background.voice_repair.current",
                    title="当前版本",
                    source="proactive_message",
                    content=spoken,
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.voice_repair.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "1. 只输出修正后的最终语音内容，不要解释。\n"
                        "2. 如果需要 <tts>...</tts>，必须补齐。\n"
                        "3. 如果要求日语语音，就让真正会被念出来的那一部分变成自然的日语，而不是普通中文。\n"
                        "4. 如果没有强制格式，也保持私聊语音的自然感。"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "voice_repair"},
        )

    def _build_voice_repair_prompt(
        self,
        *,
        spoken: str,
        requirement: dict[str, Any],
        target: str,
    ) -> str:
        persona = self._get_default_persona_prompt()
        tts_prompt = self._get_tts_prompt_text(target)
        return render_prompt_document(
            self._voice_repair_prompt_document(
                persona=persona,
                tts_prompt=tts_prompt,
                requirement_summary=str(requirement.get("summary") or ""),
                spoken=spoken,
            )
        )["user"]

    async def _build_tts_modify_components(
        self,
        spoken_text: str,
        tts_provider: Any,
        provider_settings: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[Any], str]:
        try:
            processor = getattr(self, "_process_tts_tags", None)
            if not callable(processor):
                return [], "TTS强化未接入"
            components = await processor(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
            )
        except Exception as e:
            logger.warning(f"TTS强化处理主动语音失败: {e}")
            return [], str(e)
        audio_note = self._extract_record_note(components)
        return components or [], audio_note or "已通过 TTS强化生成语音"

    def _get_tts_modify_plugin(self, config: dict[str, Any]) -> Any:
        for module_name in ("astrbot_plugin_tts_modify.main", "data.plugins.astrbot_plugin_tts_modify.main"):
            try:
                module = importlib.import_module(module_name)
                plugin_cls = getattr(module, "TTSModifyPlugin", None)
                if plugin_cls is not None:
                    return plugin_cls(self.context, config)
            except Exception:
                continue
        return None

    def _extract_record_note(self, components: list[Any]) -> str:
        for component in components or []:
            file_value = str(getattr(component, "file", "") or "").strip()
            url_value = str(getattr(component, "url", "") or "").strip()
            if file_value:
                return file_value
            if url_value:
                return url_value
        return ""

    def _strip_tts_markup(self, text: str) -> str:
        stripped = self._visible_text_without_tts_reading(text)
        stripped = stripped.replace("\r", "\n")
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        return _single_line(" ".join(lines), 120)

    def _visible_text_without_tts_reading(self, text: str, *, limit: int = 1000) -> str:
        source = str(text or "").strip()
        if not source:
            return ""
        if self._is_proactive_delivery_receipt_text(source):
            return ""
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(source, limit)
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            source = placeholder_cleaner(source)
        emotion_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if bool(runtime_persona_setting(self, "enable_tts_enhancement", False)) and callable(emotion_cleaner):
            source = emotion_cleaner(source)
        normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(normalizer) and re.search(r"</?t{2,}s\b", source, flags=re.IGNORECASE):
            try:
                source = str(normalizer(source) or source).strip()
            except Exception:
                pass
        if re.search(r"<tts\b[^>]*>.*?</tts>", source, flags=re.IGNORECASE | re.DOTALL):
            outside = re.sub(r"<tts\b[^>]*>.*?</tts>", "", source, flags=re.IGNORECASE | re.DOTALL)
            outside = re.sub(r"</?t{2,}s\b[^>]*>", "", outside, flags=re.IGNORECASE).strip()
            if re.search(r"[\u4e00-\u9fff]", outside):
                return _single_line(_strip_internal_message_blocks(
                    outside, tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
                ), limit)
            source = re.sub(r"</?t{2,}s\b[^>]*>", "", source, flags=re.IGNORECASE).strip()
        has_kana = bool(re.search(r"[\u3040-\u30ff]", source))
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", source))
        if (
            has_kana
            and has_cjk
            and runtime_persona_setting(self, "tts_voice_language", "zh") != "zh"
        ):
            units = re.findall(r".*?[。！？!?…~～]+|.+$", source, flags=re.DOTALL)
            kept: list[str] = []
            dropped = False
            for unit in units:
                cleaned = str(unit or "").strip()
                if not cleaned:
                    continue
                if re.search(r"[\u3040-\u30ff]", cleaned):
                    dropped = True
                    continue
                kept.append(cleaned)
            if dropped and kept and any(re.search(r"[\u4e00-\u9fff]", item) for item in kept):
                return _single_line(_strip_internal_message_blocks(
                    "".join(kept), tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
                ), limit)
        return _single_line(_strip_internal_message_blocks(
            source, tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
        ), limit)
