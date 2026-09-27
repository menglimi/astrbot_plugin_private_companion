# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part03Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（references）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

import re
from typing import Any

from .helpers import (
    _missing_optional_model_dependency,
    _path_text,
    _single_line,
)
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .llm_tool_actions_shared import logger
from .persona_config import runtime_persona_setting


class LlmToolActionsPhotoGeneratePart02Part03Mixin:
    """_pc_generate_photo_impl 的 references 段。"""

    async def _pc_generate_photo_impl_references(
        self,
        compact_prompt,
        content,
        event,
        group_photo_requested,
        image_size,
        inbound_photo_text,
        inherited_nai_params,
        intent_kind,
        kwargs,
        legacy_generator,
        photo_scope,
        proactive_request,
        public_receipt,
        reference_image_path,
        reference_image_paths,
        request_scope,
        requester,
        requester_id,
        scene_preset,
        send,
        structured_generator,
        tool_started_at,
        visible_caption,
        workflow_kind,
    ):
        """_pc_generate_photo_impl 段：参考图收集、合影授权、稳定化与改图/自拍补图。"""
        def bool_arg(value: Any, default: bool = True) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on", "发送", "发出", "是"}:
                return True
            if text in {"0", "false", "no", "n", "off", "不发送", "否"}:
                return False
            return default

        send_image = bool_arg(send, True)
        if send_image:
            marker = getattr(self, "_mark_smart_imagechat_skip_proactive_emoji", None)
            if callable(marker):
                marker(event)
        reference_sources: list[str] = []

        def add_reference_source(value: Any) -> None:
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("url")
            path = _path_text(value, 1000)
            if path and path not in reference_sources:
                reference_sources.append(path)

        add_reference_source(
            reference_image_path
            or kwargs.get("reference")
            or kwargs.get("image")
            or kwargs.get("image_path")
            or kwargs.get("image_url")
        )
        raw_multi_references = (
            reference_image_paths
            if reference_image_paths is not None
            else kwargs.get("reference_images", kwargs.get("images"))
        )
        if isinstance(raw_multi_references, (list, tuple, set)):
            for raw_reference in raw_multi_references:
                add_reference_source(raw_reference)
        elif raw_multi_references:
            add_reference_source(raw_multi_references)

        if group_photo_requested:
            # 合影只能由本轮图片，或用户明确点名且已有托管参考图的关系角色授权。
            # 模型传入的路径始终不参与授权，关系角色图片仍交给下游选图器处理。
            reference_sources.clear()
            reference_path = ""
            role_reference_candidates: list[dict[str, Any]] = []
            role_reference_resolver = getattr(
                self,
                "_photo_reference_role_asset_candidates",
                None,
            )
            if bool(runtime_persona_setting(self, 'enable_photo_reference_image', False)) and callable(
                role_reference_resolver
            ):
                try:
                    resolved_candidates = role_reference_resolver(
                        request_text=content,
                    )
                    if isinstance(resolved_candidates, list):
                        role_reference_candidates = [
                            candidate
                            for candidate in resolved_candidates
                            if isinstance(candidate, dict)
                            and candidate.get("kind") == "relation_role"
                            and bool(candidate.get("role_explicit_mention"))
                            and _path_text(candidate.get("path"), 1000)
                        ]
                except Exception as exc:
                    logger.info(
                        "合影关系网角色参考图解析失败，继续检查本轮图片: %s",
                        _single_line(exc, 160),
                    )
            has_named_role_reference = bool(role_reference_candidates)
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            saw_image = False
            if callable(context_resolver):
                try:
                    resolved_path, _resolved_label, saw_image = await context_resolver(event, requester_id)
                    reference_path = _path_text(resolved_path, 1000)
                except Exception as exc:
                    if not has_named_role_reference:
                        missing = _missing_optional_model_dependency(exc)
                        message = (
                            f"合影参考图解析缺少可选依赖 {missing}，请让用户重新发送或引用人物图片。"
                            if missing
                            else f"合影参考图解析失败：{_single_line(exc, 160)}"
                        )
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "success": False,
                                "generated": False,
                                "sent": False,
                                "message": message,
                                "must_not_claim_sent": True,
                                "retryable": False,
                            },
                            ensure_ascii=False,
                        )
            if not reference_path and not has_named_role_reference:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": (
                            "看到了本轮图片，但没能保存成可用的其他人物参考图；请让用户重新发送或引用人物图片。"
                            if saw_image
                            else "合影需要本轮随消息发送或引用的其他人物参考图，或明确点名已绑定可用参考图的关系网角色。Bot 单人人设图、今日穿搭图、纯文字描述或单独传入的路径都不算，已停止生成。"
                        ),
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            if reference_path:
                add_reference_source(reference_path)

        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        event_bound_resolver = getattr(self, "_photo_reference_event_bound_stable_path", None)
        resolved_reference_paths: list[str] = []
        for index, source in enumerate(reference_sources):
            # Keep the mixin compatible with lightweight/legacy hosts that do
            # not expose the optional reference normalizer. Full plugin hosts
            # still pass model-controlled sources through the untrusted path
            # guard below; Q5 managed assets use their separate ticketed sink.
            stable = source if not callable(resolver) else ""
            if callable(resolver):
                try:
                    stable = await resolver(source, stem=f"tool_{index + 1}", event=event, trusted=False)
                except Exception as exc:
                    logger.info(
                        "tool reference %s rejected: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
            if not stable and callable(event_bound_resolver):
                try:
                    stable = await event_bound_resolver(
                        event,
                        requester_id,
                        source,
                        stem=f"tool_event_{index + 1}",
                    )
                except Exception as exc:
                    logger.info(
                        "current-event reference %s could not be persisted: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
                if stable:
                    logger.info(
                        "accepted model reference after exact current-event source verification: index=%s",
                        index + 1,
                    )
            if not stable:
                logger.warning(
                    "model-controlled image reference rejected: source=%s",
                    _single_line(source, 200),
                )
                return public_receipt(
                    {
                        "status": "invalid_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这张参考图不能使用。参考图只支持当前消息里的图片、插件数据目录内的图片，或公网图片链接。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            resolved = stable
            if resolved and resolved not in resolved_reference_paths:
                resolved_reference_paths.append(resolved)
        reference_path = resolved_reference_paths[0] if resolved_reference_paths else ""
        if intent_kind == "edit" and not reference_path:
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            if callable(context_resolver):
                try:
                    try:
                        user_id = str(event.get_sender_id())
                    except Exception:
                        user_id = ""
                    resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                    if resolved_path:
                        reference_path = resolved_path
                        resolved_reference_paths = [resolved_path]
                    elif saw_image:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                            },
                            ensure_ascii=False,
                        )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if missing:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": f"改图参考图解析缺少可选依赖 {missing}，请让用户直接提供本地图片路径或图片 URL。",
                            },
                            ensure_ascii=False,
                        )
                    return public_receipt(
                        {"status": "error", "message": f"改图参考图解析失败：{_single_line(exc, 160)}"},
                        ensure_ascii=False,
                    )
            if not reference_path:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "message": "改图/重绘需要参考图。可以让用户把图片和要求一起发，或引用近期图片再说“改成……”。",
                    },
                    ensure_ascii=False,
                )
        if not reference_path and intent_kind in {"selfie", "sticker"}:
            wants_indexed_references = bool(
                re.search(
                    r"(?:第(?:[一二三四五六七八九十\d]+)张|"
                    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth)\s+"
                    r"(?:image|photo|picture))",
                    compact_prompt,
                    flags=re.I,
                )
            )
            try:
                try:
                    user_id = str(event.get_sender_id())
                except Exception:
                    user_id = ""
                saw_image = False
                if wants_indexed_references:
                    multi_resolver = getattr(
                        self,
                        "_photo_reference_images_from_command_context",
                        None,
                    )
                else:
                    multi_resolver = None
                if callable(multi_resolver):
                    images, saw_image = await multi_resolver(event, user_id, limit=8)
                    resolved_reference_paths = [
                        _path_text(item[0], 1000)
                        for item in images
                        if isinstance(item, (list, tuple))
                        and item
                        and _path_text(item[0], 1000)
                    ]
                    if resolved_reference_paths:
                        reference_path = resolved_reference_paths[0]
                else:
                    context_resolver = getattr(
                        self,
                        "_photo_reference_image_from_command_context",
                        None,
                    )
                    if callable(context_resolver):
                        resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                        if resolved_path:
                            reference_path = resolved_path
                            resolved_reference_paths = [resolved_path]
                if saw_image and not resolved_reference_paths:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                        },
                        ensure_ascii=False,
                    )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if missing:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": f"参考图解析缺少可选依赖 {missing}；如已开启参考图一致性，会改用已配置的人设参考图或今日穿搭图。",
                        },
                        ensure_ascii=False,
                    )
                return public_receipt(
                    {"status": "error", "message": f"参考图解析失败：{_single_line(exc, 160)}"},
                    ensure_ascii=False,
                )
        return _StageNext((reference_path, resolved_reference_paths, send_image))
