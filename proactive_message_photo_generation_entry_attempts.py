# -*- coding: utf-8 -*-
"""生图入口分发与尝试记账域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 264 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import inspect
import re
from .helpers import _normalize_photo_subject_owner, _path_text, _single_line
from .persona_config import runtime_persona_setting
from .photo_reference_selection import SelectionResult
from .proactive_message_photo_generation_shared import logger
from pathlib import Path
from typing import Any



class ProactiveMessagePhotoGenerationEntryAttemptsMixin:
    """生图入口分发与尝试记账域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    async def _run_photo_text_action(self, user: dict[str, Any], name: str, reason: str) -> str:
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return "photo_text：未启用"
        user_id = str(user.get("user_id") or "")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True, user=user, user_id=user_id):
            return "photo_text：主动生图不在当前配置的使用范围内,不能假装已经拍照"
        load_defer_note = self._photo_text_load_defer_note("photo_text", force_refresh=True)
        if load_defer_note:
            return f"photo_text：{load_defer_note},不能假装已经拍照"
        if not self._photo_text_available(user):
            return "photo_text：今日发图额度已用完或生图后端不可用,不能假装已经拍照"
        if not self._photo_text_available():
            return "photo_text：当前没有可用的生图后端,不能假装已经拍照"

        scene = await self._build_photo_scene_prompt(user, name, reason)
        workflow_kind = scene.get("kind", "text2img")
        normalized_workflow_kind = _single_line(workflow_kind, 40).strip().lower()
        raw_subject_owner = _normalize_photo_subject_owner(scene.get("subject_owner"))
        subject_owner = raw_subject_owner
        if not subject_owner:
            subject_owner = (
                "bot"
                if bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                else "scene"
            )
        if normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"} and subject_owner == "scene":
            subject_owner = "bot"
        non_bot_identity_owner = raw_subject_owner in {"third_party", "unknown"} or subject_owner in {
            "third_party",
            "unknown",
        }
        bot_identity_required = (
            not non_bot_identity_owner
            and (
                bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                or subject_owner == "bot"
            )
        )
        reference_image_path = ""
        reference_selection_source = ""

        def valid_reference_path(value: Any) -> str:
            """Return a local image path only when it is an existing file."""
            if isinstance(value, SelectionResult):
                value = value.selected
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("file_path")
            raw = _path_text(value, 1000)
            if not raw or re.match(r"^(?:https?|data):", raw, flags=re.I):
                return ""
            try:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    data_dir = _path_text(getattr(self, "data_dir", ""), 1000)
                    if data_dir:
                        path = Path(data_dir) / path
                path = path.resolve()
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    return ""
            except (OSError, ValueError, TypeError, RuntimeError):
                return ""
            return str(path)

        # A third-party or ambiguous owner must never inherit Bot's persona image.
        # Such scenes may proceed only with an explicitly supplied, valid reference.
        if non_bot_identity_owner:
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            reference_selection_source = "explicit_reference" if reference_image_path else ""
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：第三方或归属不明的人物只能使用对应的可验证参考图，不能套用 Bot 身份图。"
                )

        if bot_identity_required:
            # Character-bearing photo_text scenes need identity continuity even when
            # their rendering workflow is text2img rather than selfie.
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            if reference_image_path:
                reference_selection_source = "explicit_reference"
            async_reference_getter = getattr(
                self,
                "_photo_persona_reference_image_for_kind_async",
                None,
            )
            if not reference_image_path and callable(async_reference_getter):
                try:
                    selected_path = await async_reference_getter(
                        "selfie",
                        allow_daily_outfit=True,
                        requester_user_id=str(user.get("user_id") or ""),
                        request_text=_single_line(scene.get("prompt"), 900),
                        ambient_context=_single_line(scene.get("scene_context"), 900),
                    )
                    reference_image_path = valid_reference_path(selected_path)
                    if reference_image_path:
                        reference_selection_source = "selected_reference"
                except Exception as exc:
                    logger.debug(
                        "proactive photo reference selection failed: %s",
                        _single_line(exc, 160),
                    )
            if not reference_image_path:
                fallback_getter = getattr(
                    self,
                    "_photo_persona_reference_image_for_kind",
                    None,
                )
                if callable(fallback_getter):
                    try:
                        reference_image_path = valid_reference_path(
                            fallback_getter("selfie", allow_daily_outfit=False)
                        )
                    except Exception as exc:
                        logger.debug(
                            "proactive photo identity fallback failed: %s",
                            _single_line(exc, 160),
                        )
                if not reference_image_path:
                    fallback_path_getter = getattr(
                        self,
                        "_photo_persona_reference_image_path_async",
                        None,
                    )
                    if callable(fallback_path_getter):
                        try:
                            reference_image_path = valid_reference_path(
                                await fallback_path_getter()
                            )
                        except Exception as exc:
                            logger.debug(
                                "proactive photo identity path fallback failed: %s",
                                _single_line(exc, 160),
                            )
                if reference_image_path:
                    reference_selection_source = "identity_fallback"
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：需要 Bot 或其他人物的可验证参考图，不能生成无来源的人脸。"
                )
        elif subject_owner == "scene":
            scene["prompt"] = self._append_photo_negative_terms(
                scene.get("prompt", ""),
                ["people", "human figures", "faces", "silhouettes"],
                limit=900,
            )
        session_key = str(user.get("umo") or user.get("user_id") or name)
        continuity_key = self._compose_photo_continuity_key(session_key, user.get("user_id"))
        backend_name, image_path, workflow_note = await self._generate_photo_image(
            workflow_kind=workflow_kind,
            prompt_text=scene["prompt"],
            request_text=scene["prompt"],
            session_key=session_key,
            continuity_key=continuity_key,
            requester_user_id=str(user.get("user_id") or ""),
            reference_image_path=reference_image_path,
            prompt_format=_single_line(scene.get("prompt_format"), 40),
        )
        if not image_path:
            counted_attempt = self._photo_generation_failure_counts_as_attempt(workflow_note)
            if counted_attempt:
                async with self._data_lock:
                    self._note_photo_generation_attempt(user_id, image_path="")
                    scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                    if callable(scope_notifier):
                        scope_notifier(
                            proactive=True,
                            user=user,
                            user_id=user_id,
                            scope="proactive",
                        )
                    self._save_photo_generation_attempts_compat()
            return (
                "photo_text：生图失败,不能假装已经拍照\n"
                f"画面草稿：{scene['caption']}\n"
                f"失败原因：{_single_line(workflow_note, 160)}"
                + ("\n本次已计入今日生图尝试额度,避免接口失败时反复请求。" if counted_attempt else "")
            )
        async with self._data_lock:
            self._note_photo_generation_attempt(user_id, image_path=image_path)
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    proactive=True,
                    user=user,
                    user_id=user_id,
                    scope="proactive",
                )
            self._save_photo_generation_attempts_compat()
        scene_context_line = _single_line(scene.get("scene_context"), 500)
        return (
            f"photo_text：已通过 {backend_name} 生成真实图片\n"
            f"图片类型：{workflow_kind}\n"
            f"后端：{backend_name}\n"
            f"图片路径：{image_path}\n"
            f"画面：{scene['caption']}\n"
            f"图片主体归属：{subject_owner}\n"
            f"人物参考图：{('已使用（' + (reference_selection_source or 'selected_reference') + '）') if reference_image_path else '未使用'}\n"
            + (f"统一情境：{scene_context_line}\n" if scene_context_line else "")
            + f"生图提示：{_single_line(scene['prompt'], 240)}"
        )

    def _save_photo_generation_attempts_compat(self) -> None:
        """Persist photo quota changes while tolerating legacy test/host overrides."""
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            parameters = inspect.signature(saver).parameters.values()
            accepts_sections = any(
                parameter.name == "sections"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_sections = True
        if accepts_sections:
            saver(sections={"users", "photo_generation_scope_attempts"})
        else:
            saver()

    def _photo_generation_failure_counts_as_attempt(self, note: str) -> bool:
        text = _single_line(note, 500)
        if not text:
            return False
        count_tokens = (
            "HTTP",
            "超时",
            "请求",
            "接口",
            "上游",
            "upstream",
            "返回格式",
            "未返回",
            "返回空",
            "下载",
            "保存",
            "响应不是图片",
            "输出不是图片",
            "工作流完成但图片",
            "Error code",
            "Exception",
        )
        if any(token in text for token in count_tokens):
            return True
        skip_tokens = (
            "未启用",
            "未配置",
            "不可用或未配置",
            "后端不可用",
            "插件不可用",
            "工作流名",
            "未找到匹配工作流",
            "电脑高负荷",
            "负载偏高",
            "今日发图额度",
            "文本/聊天模型",
            "请改成图片模型",
        )
        return not any(token in text for token in skip_tokens)
