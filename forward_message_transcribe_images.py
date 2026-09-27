# -*- coding: utf-8 -*-
"""ForwardMessageTranscribeImagesMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 296 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

import asyncio
import time
from .forward_message_shared import logger
from astrbot.api.event import AstrMessageEvent
from .forward_message_shared import PromptRenderMode
from .forward_message_shared import PromptSection
from .forward_message_shared import _safe_int
from .forward_message_shared import _single_line
from .forward_message_shared import _strip_internal_message_blocks
from .forward_message_shared import prompt_section
from .forward_message_shared import render_prompt_sections
from .forward_message_shared import runtime_persona_setting



class ForwardMessageTranscribeImagesMixin:
    """ForwardMessageTranscribeImagesMixin（从 ForwardMessageMixin 拆出）。"""


    async def _transcribe_forward_message_images(self, event: AstrMessageEvent, image_sources: list[str]) -> str:
        if not runtime_persona_setting(self, "forward_message_image_vision", True):
            logger.info("合并/引用图片视觉跳过: forward_message_image_vision=false")
            return ""
        limit = max(0, _safe_int(runtime_persona_setting(self, "forward_message_image_limit", 4), 4, 0))
        if limit <= 0:
            logger.info("合并/引用图片视觉跳过: forward_message_image_limit=%s", limit)
            return ""
        original_sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:limit]
        if not original_sources:
            logger.info("合并/引用图片视觉跳过: 未抽取到图片源")
            return ""
        sources = await self._prepare_private_image_sources_for_model(
            original_sources,
            namespace="forward_vision",
        )
        if not sources:
            logger.info(
                "合并/引用图片视觉跳过: 图片源无法转为模型可读源 original=%s",
                len(original_sources),
            )
            return ""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        image_items, source_image_count, has_gif_frames = self._private_image_model_image_items_with_meta(sources)
        image_keys = [key for key, _ in image_items]
        image_urls = [url for _, url in image_items]
        if not image_urls:
            logger.info(
                "合并/引用图片视觉跳过: 已准备图片但无模型可用 URL sources=%s prepared=%s",
                len(original_sources),
                len(sources),
            )
            return ""
        image_aliases = self._private_image_cache_aliases_for_sources([*original_sources, *sources])
        original_image_keys = self._private_image_cache_image_keys(original_sources or sources)
        if original_image_keys:
            image_keys = original_image_keys
        image_count = source_image_count or len(original_sources) or len(sources)
        gif_hint = (
            "如果同一张动态 GIF 被抽成多帧,这些帧属于同一张动图；请按整体动图主体判断归属,不要因某一帧局部相似就误判。"
            if has_gif_frames
            else ""
        )
        default_section = prompt_section(
            key="background.forward_message_image_vision",
            title="合并消息图片视觉摘要",
            source="forward_message",
            content=(
                "请按出现顺序把合并消息里的图片压缩成短摘要。每张图只写一行,不要写标题、分析过程或长篇描述。\n"
                "格式：第N张：<图片类型>；内容=<可见文字/主体/动作/关键细节,125字内>；表达=<用户可能借图表达的情绪、态度、疑问、用途或梗,125字内；表情包/贴纸/GIF优先写它在表达什么>；归属=<疑似当前角色/非当前角色/无法判断,只写标签>。\n"
                "完整性规则：每张图都要同时保留客观内容和表达意图；这是在原有基础上的增强,不是二选一。"
                "若是照片/截图/漫画/聊天记录,内容描述更细一点；若是表情包/贴纸/GIF,表达意图和情绪梗分析更多一点。看不清就写看不清；不要猜测人物关系。"
                "即使表情包疑似当前角色,也不要让“这是当前角色”盖过它的文字、动作、表情和梗点；归属只放在归属字段。"
                "若图中有游戏/作品/角色/活动/节日/日期等文字,必须尽量照抄可见原文；不能把同系列作品或相近活动名互换,不能用联网印象补全看不清的内容。"
                "如果同一张动态 GIF 被抽成多帧,请把连续帧当作同一个动态表情包理解,概括动作/表情变化。"
                f"{gif_hint}"
            ),
        )
        default_prompt = render_prompt_sections(
            [default_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        attempts = 0
        seen_providers: set[str] = set()
        skipped_providers: list[str] = []
        candidates = self._private_image_visual_provider_candidates(umo)
        primary_visual_id = next(
            (_single_line(item[0], 160) for item in candidates if len(item) >= 2 and item[1] == "plugin_vision"),
            "",
        )
        fallback_visual_id = next(
            (_single_line(item[0], 160) for item in candidates if len(item) >= 2 and item[1] == "plugin_vision_fallback"),
            "",
        )
        visual_key_getter = getattr(self, "_private_image_visual_provider_card_key", None)
        visual_provider_key = visual_key_getter() if callable(visual_key_getter) else "PLUGIN_VISION_PROVIDER_ID"
        for provider_id, provider_source, _configured_prompt in candidates:
            provider_id = _single_line(provider_id, 160)
            if not provider_id:
                skipped_providers.append(f"{provider_source}:empty")
                continue
            if provider_id in seen_providers:
                continue
            seen_providers.add(provider_id)
            if self._private_image_provider_in_failure_cooldown(provider_id, provider_source):
                skipped_providers.append(f"{provider_source}:cooldown")
                continue
            provider = self._private_image_provider_by_id(provider_id)
            if provider is None or not self._provider_supports_image(provider):
                skipped_providers.append(f"{provider_source}:no_image_provider")
                continue
            attempts += 1
            prompt = default_prompt
            recognition_builder = getattr(
                self,
                "_private_image_self_recognition_context_prompt_section",
                None,
            )
            recognition_section = recognition_builder() if callable(recognition_builder) else None
            self_recognition_prompt = (
                render_prompt_sections(
                    [recognition_section],
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
                if isinstance(recognition_section, PromptSection)
                else self._private_image_self_recognition_context_prompt()
            )
            if self_recognition_prompt and self_recognition_prompt not in prompt:
                prompt = f"{prompt}\n\n{self_recognition_prompt}"
            # This visual call bypasses ``_llm_call``; apply plugin-owned task
            # instructions before both cache-key construction and dispatch.
            task_prompt_customized = False
            prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
            if callable(prompt_applier):
                original_prompt = prompt
                prompt, _unused_system_prompt = prompt_applier(
                    "forward_message_image_vision",
                    prompt,
                    None,
                    flatten_system_prompt=True,
                )
                task_prompt_customized = prompt != original_prompt
            cache_prompt_sig = self._private_image_vision_cache_prompt_signature(prompt)
            cache_key = self._private_image_vision_cache_key(image_keys, provider_id, cache_prompt_sig, scope="forward_image")
            cached_text = self._get_private_image_vision_cache(
                cache_key,
                provider_id=provider_id,
                image_keys=image_keys,
                image_aliases=image_aliases,
                image_count=image_count,
                scope="forward_image",
                allow_image_key_fallback=not task_prompt_customized,
                prompt=cache_prompt_sig,
            )
            if cached_text:
                logger.info(
                    "合并消息图片视觉命中缓存: provider=%s images=%s preview=%s",
                    provider_id,
                    len(image_urls),
                    _single_line(cached_text, 220),
                )
                return cached_text
            if not self._can_run_llm_task(provider_id, task="forward_message_image_vision"):
                self._record_llm_budget_skip(provider_id=provider_id, task="forward_message_image_vision", prompt=prompt)
                skipped_providers.append(f"{provider_source}:budget")
                continue
            try:
                start = time.time()
                token_skip_getter = getattr(self, "_model_token_limit_should_skip_primary", None)
                if callable(token_skip_getter) and token_skip_getter(
                    task="forward_message_image_vision",
                    provider_id=provider_id,
                    primary_provider_id=primary_visual_id,
                    fallback_provider_id=fallback_visual_id,
                    provider_key=visual_provider_key,
                    prompt=prompt,
                    max_tokens=260,
                    image_count=len(image_urls),
                ):
                    self._record_llm_usage(
                        provider_id=provider_id,
                        task="forward_message_image_vision",
                        prompt=prompt,
                        completion="",
                        resp=None,
                        elapsed_ms=0,
                        success=False,
                        error="model_token_limit_exceeded",
                        budget_exempt=True,
                    )
                    skipped_providers.append(f"{provider_source}:token_limit")
                    continue
                timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
                override_timeout = (
                    timeout_getter(
                        task="forward_message_image_vision",
                        provider_id=provider_id,
                        timeout_key=visual_provider_key,
                    )
                    if callable(timeout_getter)
                    else None
                )
                timeout_source = (
                    f"model_timeout_overrides.{visual_provider_key}"
                    if override_timeout is not None
                    else "forward_message_image_vision_timeout_seconds"
                )
                timeout = max(
                    0.0,
                    float(
                        override_timeout
                        if override_timeout is not None
                        else (runtime_persona_setting(self, "forward_message_image_vision_timeout_seconds", 60.0) or 0.0)
                    ),
                )
                if timeout > 0:
                    result = await asyncio.wait_for(provider.text_chat(prompt=prompt, image_urls=image_urls, max_tokens=260), timeout=timeout)
                else:
                    result = await provider.text_chat(prompt=prompt, image_urls=image_urls, max_tokens=260)
                text = str(getattr(result, "completion_text", result) or "").strip()
                cleaned_text = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 900)
                if not cleaned_text:
                    empty_note = "合并消息识图模型返回空摘要"
                    self._record_llm_usage(
                        provider_id=provider_id,
                        task="forward_message_image_vision",
                        prompt=prompt,
                        completion=text,
                        resp=result,
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        error=empty_note,
                        budget_exempt=True,
                    )
                    self._mark_private_image_provider_failure(
                        provider_id,
                        provider_source,
                        empty_note,
                        task="forward_message_image_vision",
                    )
                    logger.info(
                        "合并消息图片视觉返回空摘要,已尝试下一个 provider: provider=%s source=%s",
                        provider_id,
                        provider_source,
                    )
                    continue
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion=text,
                    resp=result,
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=True,
                    budget_exempt=True,
                )
                self._clear_private_image_provider_failure(provider_id, provider_source)
                logger.info(
                    "合并消息图片视觉完成: provider=%s source=%s images=%s chars=%s preview=%s",
                    provider_id,
                    provider_source,
                    len(image_urls),
                    len(cleaned_text),
                    _single_line(cleaned_text, 220),
                )
                self._set_private_image_vision_cache(
                    cache_key,
                    cleaned_text,
                    provider_id=provider_id,
                    image_keys=image_keys,
                    image_aliases=image_aliases,
                    image_count=image_count,
                    prompt=cache_prompt_sig,
                    scope="forward_image",
                )
                return cleaned_text
            except asyncio.TimeoutError as exc:
                elapsed_ms = int((time.time() - start) * 1000) if "start" in locals() else 0
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=f"timeout after {timeout:.1f}s",
                    budget_exempt=True,
                )
                logger.warning(
                    "合并消息图片视觉超时,本轮尝试下一个 provider；不会禁用后续图片调用: provider=%s source=%s timeout=%.1fs timeout_source=%s",
                    provider_id,
                    provider_source,
                    timeout,
                    timeout_source,
                )
                continue
            except Exception as exc:
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000) if "start" in locals() else 0,
                    success=False,
                    error=_single_line(exc, 180),
                    budget_exempt=True,
                )
                self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="forward_message_image_vision")
                continue
        logger.warning(
            "合并消息图片视觉失败: 所有候选 provider 均不可用或失败 attempts=%s skipped=%s images=%s",
            attempts,
            ",".join(skipped_providers[:8]) or "-",
            len(image_urls),
        )
        return ""
