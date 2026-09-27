# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiTtsPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_tts.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 398 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiTtsMixin）。
"""
from __future__ import annotations

from .page_api_tts_shared import TTS_PROVIDER_SECRET_KEYS, logger
from .page_api_tts_shared import Any
from .page_api_tts_shared import MessageChain
from .page_api_tts_shared import Path
from .page_api_tts_shared import SimpleNamespace
from .page_api_tts_shared import _redact_outbound_secrets
from .page_api_tts_shared import deepcopy
from .page_api_tts_shared import re
from .page_api_tts_shared import time



class PrivateCompanionPageApiTtsPart01Mixin:
    """PrivateCompanionPageApiTtsPart01Mixin（从 PrivateCompanionPageApiTtsMixin 拆出）。"""


    async def _run_tts_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = getattr(self.plugin, "context", None)
        if context is None:
            error = "AstrBot context 不可用"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "generated": False,
                "delivered": False,
                "delivery_umo": "",
                "delivery_error": error,
                "error": error,
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._preferred_tts_test_umo(
            users,
            owner_only=True,
            resolve_delivery_route=True,
        )
        if not umo:
            error = "没有找到主要用户的有效私聊会话；请先设置主要用户并让 Bot 收到一条该用户的私聊消息"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "generated": False,
                "delivered": False,
                "delivery_umo": "",
                "delivery_error": error,
                "error": error,
            }
        requested_umo = self._single_line(payload.get("umo"), 180)
        if requested_umo and requested_umo != umo:
            logger.info(
                "TTS 排障测试忽略非主要用户目标: requested=%s owner=%s",
                requested_umo,
                umo,
            )
        config: dict[str, Any] = {}
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(umo) if umo else getter()
                if not isinstance(config, dict):
                    config = {}
            except Exception:
                config = {}
        provider_getter = getattr(context, "get_using_tts_provider", None)
        tts_provider = None
        if callable(provider_getter):
            try:
                tts_provider = provider_getter(umo) if umo else provider_getter()
            except Exception:
                tts_provider = None
        resolver = getattr(self.plugin, "_resolve_tts_synthesis_provider", None)
        if callable(resolver):
            try:
                tts_provider = resolver(SimpleNamespace(unified_msg_origin=umo), tts_provider)
            except Exception:
                pass
        if tts_provider is None:
            error = "当前没有可用的 AstrBot TTS Provider 或 MiMo Voice Clone 联动"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "umo": umo,
                "generated": False,
                "delivered": False,
                "delivery_umo": umo,
                "delivery_error": error,
                "error": error,
            }
        provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
        spoken = self._single_line(payload.get("text"), 240) or "这是一条排障测试语音，用来确认 TTS 生成链路可以跑通。"
        record_builder = getattr(self.plugin, "_tts_record_component", None)
        started = time.time()
        if callable(record_builder):
            component = await record_builder(
                spoken,
                tts_provider,
                provider_settings,
                config,
                source_text=spoken,
            )
            refs_getter = getattr(self.plugin, "_tts_record_refs", None)
            refs = refs_getter(component) if callable(refs_getter) and component is not None else []
        else:
            audio_path = await tts_provider.get_audio(spoken)
            component = None
            refs = [str(audio_path)] if audio_path else []
        audio_ref = self._single_line(refs[0] if refs else "", 260)
        exists = False
        file_size = 0
        if audio_ref and not re.match(r"^https?://", audio_ref, flags=re.IGNORECASE):
            try:
                audio_file = Path(audio_ref)
                exists = audio_file.exists()
                file_size = audio_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        else:
            exists = bool(audio_ref)
        generated = bool(component is not None and audio_ref and exists)
        delivered = False
        delivery_error = ""
        steps = [
            {
                "name": "生成音频",
                "status": "ok" if generated else "error",
                "detail": "TTS Provider 已返回有效语音组件" if generated else "TTS Provider 未返回可投递的有效语音组件",
            }
        ]
        if generated:
            delivered, delivery_error = await self._deliver_tts_test_component(umo, component)
            steps.append(
                {
                    "name": "投递语音",
                    "status": "ok" if delivered else "error",
                    "detail": "测试语音已发送到主要用户私聊" if delivered else (delivery_error or "发送链路未返回成功回执"),
                }
            )
            if delivered:
                logger.info(
                    "TTS 排障测试语音投递成功: umo=%s",
                    self._single_line(umo, 140),
                )
            else:
                logger.warning(
                    "TTS 排障测试语音投递失败: umo=%s error=%s",
                    self._single_line(umo, 140),
                    self._single_line(delivery_error, 180),
                )
        elapsed_ms = int((time.time() - started) * 1000)
        provider_id = self._provider_id(tts_provider)
        provider_label = self._provider_name(tts_provider, provider_id) if provider_id else getattr(tts_provider, "__class__", type(tts_provider)).__name__
        error = ""
        if not generated:
            error = "TTS provider 未返回可投递的有效语音组件"
        elif not delivered:
            error = delivery_error or "测试语音投递失败"
        return {
            "ok": bool(generated and delivered),
            "title": "TTS 生成与投递测试",
            "umo": umo,
            "provider": self._single_line(provider_label, 100),
            "path": audio_ref,
            "file_size": file_size,
            "generated": generated,
            "delivered": delivered,
            "delivery_umo": umo,
            "delivery_error": self._single_line(delivery_error, 220),
            "steps": steps,
            "detail": "已生成语音组件并发送到主要用户私聊" if delivered else "语音组件已生成，但未能发送到主要用户私聊" if generated else "未生成可投递的语音组件",
            "text": spoken,
            "elapsed_ms": elapsed_ms,
            "error": self._single_line(error, 220),
        }

    async def _deliver_tts_test_component(self, umo: str, component: Any) -> tuple[bool, str]:
        sender = getattr(self.plugin, "_send_chain_components", None)
        if callable(sender):
            try:
                sent = await sender(
                    umo,
                    [component],
                    apply_decorating_hooks=False,
                )
            except Exception as exc:
                return False, self._single_line(_redact_outbound_secrets(str(exc), self.plugin), 600) or exc.__class__.__name__
            if sent is True:
                return True, ""
            return False, "插件发送链路返回 False，平台未确认接收测试语音"

        context = getattr(self.plugin, "context", None)
        fallback = getattr(context, "send_message", None) if context is not None else None
        if not callable(fallback):
            return False, "插件发送链路和 AstrBot context.send_message 均不可用"
        try:
            sent = await fallback(umo, MessageChain([component]))
        except Exception as exc:
            return False, self._single_line(_redact_outbound_secrets(str(exc), self.plugin), 600) or exc.__class__.__name__
        if sent is False:
            return False, "AstrBot 核心发送返回 False，平台未确认接收测试语音"
        return True, ""

    def _preferred_tts_test_umo(
        self,
        users: dict[str, Any],
        *,
        owner_only: bool = False,
        resolve_delivery_route: bool = False,
    ) -> str:
        fallback = ""
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            resolved_user_id = str(item.get("user_id") or user_id)
            stored_umo = self._single_line(item.get("umo"), 180)
            if not stored_umo and not resolve_delivery_route:
                continue
            role = self.plugin._private_user_role(item, resolved_user_id)
            if role != "owner":
                if not owner_only and not fallback:
                    fallback = stored_umo
                continue
            candidates: list[str] = []
            if resolve_delivery_route:
                route_resolver = getattr(self.plugin, "_private_delivery_umo_for_user_id", None)
                if callable(route_resolver):
                    try:
                        candidates.append(self._single_line(route_resolver(resolved_user_id), 180))
                    except Exception as exc:
                        logger.warning(
                            "TTS 排障测试解析主要用户投递会话失败: user=%s error=%s",
                            self._single_line(resolved_user_id, 80),
                            self._single_line(exc, 160),
                        )
            candidates.append(stored_umo)
            for candidate in candidates:
                if not owner_only or self._is_private_test_umo(candidate):
                    return candidate
            if not owner_only and not fallback:
                fallback = stored_umo
        return "" if owner_only else fallback

    def _available_tts_provider_items(self) -> list[dict[str, Any]]:
        context = getattr(self.plugin, "context", None)
        get_all = getattr(context, "get_all_tts_providers", None)
        try:
            providers = list(get_all() or []) if callable(get_all) else []
        except Exception:
            providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            providers = list(getattr(manager, "tts_provider_insts", None) or [])

        using_id = ""
        get_using = getattr(context, "get_using_tts_provider", None)
        if callable(get_using):
            try:
                using_id = self._provider_id(get_using())
            except Exception:
                using_id = ""

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for provider in providers:
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider),
                    "model": self._provider_model(provider),
                    "is_default": provider_id == using_id,
                }
            )
        items.sort(key=lambda item: (not item["is_default"], item["name"].lower(), item["id"].lower()))
        return items

    def _tts_provider_manager(self) -> Any:
        context = getattr(self.plugin, "context", None)
        manager = getattr(context, "provider_manager", None)
        if manager is None:
            raise RuntimeError("当前 AstrBot 未提供 ProviderManager")
        return manager

    @staticmethod
    def _is_tts_provider_config(config: Any) -> bool:
        if not isinstance(config, dict):
            return False
        provider_type = str(config.get("provider_type") or "").strip().lower()
        return provider_type in {"text_to_speech", "tts"}

    @staticmethod
    def _tts_provider_field_type(value: Any, metadata: dict[str, Any]) -> str:
        field_type = str(metadata.get("type") or "").strip().lower()
        if field_type:
            return field_type
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "list"
        return "string"

    @staticmethod
    def _tts_provider_field_group(key: str) -> str:
        lowered = str(key or "").lower()
        if lowered in {"api_base", "api_key", "proxy", "timeout", "appid", "minimax-group-id"}:
            return "connection"
        if any(
            marker in lowered
            for marker in (
                "model",
                "voice",
                "character",
                "reference",
                "emotion",
                "language",
                "text_lang",
                "prompt_text_lang",
                "style",
                "role",
                "format",
                "dialect",
                "speed",
                "pitch",
                "volume",
                "rate",
            )
        ):
            return "voice"
        return "advanced"

    @staticmethod
    def _tts_provider_secret_field(key: str) -> bool:
        lowered = str(key or "").strip().lower()
        return lowered in TTS_PROVIDER_SECRET_KEYS or any(
            marker in lowered
            for marker in (
                "api-key",
                "api_key",
                "api_token",
                "access_token",
                "access_key",
                "secret_key",
                "subscription_key",
                "password",
            )
        ) or lowered in {
            "key",
            "token",
            "secret",
        }

    @staticmethod
    def _tts_provider_fallback_label(key: str) -> str:
        labels = {
            "api_key": "API Key",
            "api_base": "API 地址",
            "proxy": "代理地址",
            "timeout": "超时时间",
            "model": "模型",
            "appid": "App ID",
            "openai-tts-voice": "音色",
            "mimo-tts-voice": "音色",
            "mimo-tts-format": "输出格式",
            "mimo-tts-style-prompt": "风格提示词",
            "mimo-tts-dialect": "方言",
            "mimo-tts-seed-text": "种子文本",
            "edge-tts-voice": "音色",
            "rate": "语速",
            "volume": "音量",
            "pitch": "音调",
            "fishaudio-tts-character": "角色名称",
            "fishaudio-tts-reference-id": "参考模型 ID",
            "dashscope_tts_voice": "音色",
            "azure_tts_subscription_key": "订阅密钥",
            "azure_tts_region": "服务区域",
            "volcengine_cluster": "集群",
            "volcengine_voice_type": "音色 ID",
            "gemini_tts_api_key": "API Key",
            "gemini_tts_api_base": "API 地址",
            "gemini_tts_timeout": "超时时间",
            "gemini_tts_model": "模型",
            "gemini_tts_prefix": "朗读前缀",
            "gemini_tts_voice_name": "音色",
            "elevenlabs-tts-voice-id": "Voice ID",
            "elevenlabs-tts-output-format": "输出格式",
            "elevenlabs-tts-stability": "稳定度",
            "elevenlabs-tts-similarity-boost": "相似度增强",
            "elevenlabs-tts-style": "风格强度",
            "elevenlabs-tts-use-speaker-boost": "启用说话人增强",
        }
        if key in labels:
            return labels[key]
        return str(key or "").replace("_", " ").replace("-", " ").strip()

    @staticmethod
    def _tts_provider_fallback_hint(key: str) -> str:
        hints = {
            "api_key": "密钥不会在页面回显；留空保存会保留现有值。",
            "api_base": "服务接口地址，使用官方服务时通常保持默认。",
            "proxy": "可选代理地址，支持 HTTP、HTTPS 或 SOCKS5；留空保存会保留现有值。",
            "timeout": "单次语音合成请求的超时时间。",
        }
        return hints.get(str(key or ""), "")

    @staticmethod
    def _tts_provider_metadata_unresolved(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        return (
            lowered.startswith("provider_group.")
            or lowered.startswith("config.")
            or lowered.endswith(".description")
            or lowered.endswith(".hint")
        )
