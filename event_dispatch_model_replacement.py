# -*- coding: utf-8 -*-
"""模型替换域。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 293 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import inspect
from .event_dispatch_shared import logger
from .helpers import _single_line
from .model_routing import CURRENT_MODEL_REPLACEMENT_SOURCES, build_rules, find_route, scope_allows
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_provider_id
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.entities import LLMResponse
from typing import Any



class EventDispatchModelReplacementMixin:
    """模型替换域（从 EventDispatchMixin 拆出）。"""


    def _model_replacement_rules_for_event(self) -> list[Any]:
        rules = getattr(self, "model_replacement_rules", None)
        if isinstance(rules, list) and rules:
            return rules
        # Compatibility bridge for installations that still keep the old
        # keyword-model-router plugin enabled while migrating its settings.
        context = getattr(self, "context", None)
        getter = getattr(context, "get_registered_star", None)
        metadata = None
        if callable(getter):
            try:
                metadata = getter("astrbot_plugin_keyword_model_router")
            except Exception:
                metadata = None
        legacy = getattr(getattr(metadata, "star_cls", None), "config", None)
        raw_rules = legacy.get("route_rules", []) if isinstance(legacy, dict) else []
        parsed, warnings = build_rules(raw_rules)
        for warning in warnings:
            logger.warning("兼容旧关键词换模规则：%s", warning)
        return parsed

    @staticmethod
    def _model_replacement_event_extra(event: AstrMessageEvent, key: str, default: Any = None) -> Any:
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                value = getter(key, default)
                if value is not None:
                    return value
            except Exception:
                pass
        return getattr(event, key, default)

    @staticmethod
    def _set_model_replacement_event_extra(event: AstrMessageEvent, key: str, value: Any) -> None:
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(key, value)
                return
            except Exception:
                pass
        try:
            setattr(event, key, value)
        except Exception:
            pass

    def _model_replacement_provider_exists(self, provider_id: str) -> bool:
        value = str(provider_id or "").strip()
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not value or not callable(getter):
            return False
        try:
            return getter(value) is not None
        except Exception:
            return False

    def _model_replacement_provider_model(self, provider_id: str) -> str:
        value = str(provider_id or "").strip()
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not value or not callable(getter):
            return ""
        try:
            provider = getter(value)
            model_getter = getattr(provider, "get_model", None)
            if not callable(model_getter):
                return ""
            return str(model_getter() or "").strip()[:160]
        except Exception:
            return ""

    async def _prepare_model_replacement_sources(self, event: AstrMessageEvent) -> list[tuple[str, str]]:
        sources: list[tuple[str, str]] = []
        message = getattr(event, "message_str", "")
        if isinstance(message, str) and message.strip():
            sources.append(("wake_message", message))
        for field in (
            "private_companion_image_caption_route_text",
            "private_companion_delayed_image_vision_text",
            "private_companion_reply_image_vision_text",
        ):
            value = getattr(event, field, "")
            if isinstance(value, str) and value.strip():
                sources.append(("companion_image_caption", value))
        if not any(source == "companion_image_caption" for source, _ in sources):
            prepare = getattr(self, "prepare_keyword_model_router_image_caption", None)
            if callable(prepare):
                try:
                    caption = prepare(event)
                    if inspect.isawaitable(caption):
                        caption = await caption
                    if isinstance(caption, str) and caption.strip():
                        sources.append(("companion_image_caption", caption))
                except Exception as exc:
                    logger.debug("模型替换读取图片转述失败：%s", _single_line(exc, 120))
        return sources

    async def route_model_replacement_before_agent(self, event: AstrMessageEvent, *args: Any, **kwargs: Any) -> None:
        """Select the conversation Provider before AstrBot builds its agent."""
        if not bool(getattr(self, "enabled", False)):
            return
        stage_key, stage_provider = self._relationship_stage_provider_for_event(event)
        if stage_provider:
            self._set_model_replacement_event_extra(
                event,
                "selected_provider",
                stage_provider,
            )
            self._set_model_replacement_event_extra(
                event,
                "selected_model",
                self._model_replacement_provider_model(stage_provider) or None,
            )
            self._set_model_replacement_event_extra(
                event,
                "private_companion_relationship_stage_provider_route",
                {"stage_key": stage_key, "provider_id": stage_provider},
            )
            return
        sources = await self._prepare_model_replacement_sources(event)
        try:
            token = CURRENT_MODEL_REPLACEMENT_SOURCES.set(tuple(sources))
            setattr(event, "private_companion_model_replacement_sources_token", token)
        except Exception:
            pass
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), "conversation"):
            return
        match = find_route(self._model_replacement_rules_for_event(), sources)
        provider_id = ""
        model = ""
        if match is not None:
            provider_id = str(match.rule.provider_id or "").strip()
            model = str(match.rule.model or "").strip()
            if not self._model_replacement_provider_exists(provider_id):
                logger.warning("模型替换规则目标 Provider 不存在，保留原路由：%s", provider_id)
                provider_id = ""
        if not provider_id:
            getter = getattr(self, "_default_chat_provider_id", None)
            if callable(getter):
                try:
                    provider_id = str(getter(str(getattr(event, "unified_msg_origin", "") or "")) or "").strip()
                except Exception:
                    provider_id = ""
        peak_router = getattr(self, "_apply_deepseek_peak_replacement", None)
        if provider_id and callable(peak_router):
            provider_id = peak_router(provider_id, target="conversation")
        if not provider_id:
            return
        self._set_model_replacement_event_extra(event, "selected_provider", provider_id)
        self._set_model_replacement_event_extra(event, "selected_model", model or None)
        self._set_model_replacement_event_extra(
            event,
            "private_companion_model_replacement_route",
            {
                "provider_id": provider_id,
                "model": model,
                "matched_keyword": match.matched_keyword if match else "",
                "source": match.source if match else "deepseek_peak",
            },
        )

    async def clear_model_replacement_context(self, event: AstrMessageEvent, resp: LLMResponse, *args: Any, **kwargs: Any) -> None:
        token = getattr(event, "private_companion_model_replacement_sources_token", None)
        if token is None:
            return
        try:
            CURRENT_MODEL_REPLACEMENT_SOURCES.reset(token)
            delattr(event, "private_companion_model_replacement_sources_token")
        except Exception:
            pass

    def _relationship_stage_provider_for_event(
        self, event: AstrMessageEvent
    ) -> tuple[str, str]:
        if not bool(
            getattr(self, "enable_relationship_stage_provider_routing", False)
        ):
            return "", ""
        routes = getattr(self, "relationship_stage_provider_routes", {})
        try:
            is_private = self._safe_event_is_private(event)
            raw_sender_id = self._safe_event_sender_id(event)
            if is_private:
                resolver = getattr(self, "_private_user_id_for_event", None)
                sender_id = (
                    resolver(event)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
                users = (
                    self.data.get("users", {})
                    if isinstance(getattr(self, "data", None), dict)
                    else {}
                )
                current_user = (
                    users.get(sender_id)
                    if sender_id and isinstance(users, dict)
                    else None
                )
            else:
                projection_getter = getattr(
                    self, "_req039_group_observation_projection", None
                )
                current_user = (
                    projection_getter(
                        event,
                        sender_id=raw_sender_id,
                        sender_name=self._sender_display_name(event),
                    )
                    if callable(projection_getter)
                    else None
                )
            if not isinstance(current_user, dict):
                return "", ""
            current_user = self._lab_fixture_relationship_view(event, current_user)
            if not isinstance(current_user, dict):
                return "", ""
            stage_key, provider_id = relationship_stage_provider_id(
                routes,
                current_user.get("relationship_score", 0),
                runtime_persona_setting(self, "relationship_stage_policy", None),
                previous_stage_key=current_user.get("relationship_phase_key")
                or current_user.get("relationship_stage_key"),
                owner_exclusive=(
                    str(current_user.get("relationship_mode") or "")
                    == "owner_exclusive"
                ),
            )
            if not provider_id or not self._model_replacement_provider_exists(
                provider_id
            ):
                return stage_key, ""
            return stage_key, provider_id
        except Exception:
            return "", ""

    async def enforce_model_replacement_request(self, event: AstrMessageEvent, req: ProviderRequest, *args: Any, **kwargs: Any) -> None:
        if req is None or not bool(getattr(self, "enabled", False)):
            return
        self._set_model_replacement_event_extra(event, "provider_request", req)
        stage_route = self._model_replacement_event_extra(
            event,
            "private_companion_relationship_stage_provider_route",
            {},
        )
        stage_provider = (
            str(stage_route.get("provider_id") or "").strip()
            if isinstance(stage_route, dict)
            else ""
        )
        stage_key = (
            str(stage_route.get("stage_key") or "").strip()
            if isinstance(stage_route, dict)
            else ""
        )
        # AstrBot versions without the pre-agent waiting hook still resolve
        # the same stage route here before the request is dispatched.
        if not stage_provider:
            stage_key, stage_provider = self._relationship_stage_provider_for_event(event)
        if stage_provider:
            selected_provider = stage_provider
            selected_model = self._model_replacement_provider_model(stage_provider)
            self._set_model_replacement_event_extra(
                event,
                "selected_provider",
                selected_provider,
            )
            self._set_model_replacement_event_extra(
                event,
                "selected_model",
                selected_model or None,
            )
            self._set_model_replacement_event_extra(
                event,
                "private_companion_relationship_stage_provider_route",
                {"stage_key": stage_key, "provider_id": selected_provider},
            )
        else:
            selected_provider = str(
                self._model_replacement_event_extra(
                    event, "selected_provider", ""
                )
                or ""
            ).strip()
            selected_model = self._model_replacement_event_extra(
                event, "selected_model", None
            )
        if selected_provider:
            try:
                setattr(req, "provider_id", selected_provider)
            except Exception:
                pass
        if stage_provider:
            try:
                req.model = selected_model or None
            except Exception:
                pass
        elif selected_model:
            try:
                req.model = str(selected_model).strip()
            except Exception:
                pass
