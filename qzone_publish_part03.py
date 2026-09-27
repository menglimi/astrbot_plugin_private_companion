# -*- coding: utf-8 -*-
"""QzonePublishPart03Mixin。

由 tools/split_mixin_domain.py 从 qzone_publish.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 294 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzonePublishMixin）。
"""
from __future__ import annotations

from .qzone_publish_shared import logger
from .qzone_publish_shared import Any
from .qzone_publish_shared import Path
from .qzone_publish_shared import PromptRenderMode
from .qzone_publish_shared import _now_ts
from .qzone_publish_shared import _path_text
from .qzone_publish_shared import _safe_float
from .qzone_publish_shared import _single_line
from .qzone_publish_shared import prompt_section
from .qzone_publish_shared import random
from .qzone_publish_shared import re
from .qzone_publish_shared import render_prompt_sections
from .qzone_publish_shared import runtime_persona_setting



class QzonePublishPart03Mixin:
    """QzonePublishPart03Mixin（从 QzonePublishMixin 拆出）。"""


    async def _maybe_generate_qzone_publish_image(
        self,
        *,
        post_text: str,
        reason: str,
        daily_state: dict[str, Any] | None = None,
        current_item: Any = None,
        diary_context: str = "",
        state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> list[str]:
        reusable = [] if force else self._qzone_reusable_generated_image(state if isinstance(state, dict) else {}, reason, post_text)
        if reusable:
            self._qzone_note_publish_image_status(state, reason, "reused", "复用上次待发布配图", path=reusable[0])
            return reusable
        if not (
            getattr(self, "enable_qzone_generated_image_publish", False)
            and getattr(self, "enable_qzone_integration", False)
        ):
            self._qzone_note_publish_image_status(state, reason, "skipped:disabled", "QQ 空间配图开关未开启")
            return []
        probability = max(0.0, min(1.0, _safe_float(getattr(self, "qzone_generated_image_probability", 0.25), 0.25)))
        if not force and (probability <= 0 or random.random() > probability):
            self._qzone_note_publish_image_status(state, reason, "skipped:probability", f"未命中配图概率 {probability:.0%}")
            return []
        if callable(getattr(self, "_daily_token_soft_limit_should_defer", None)) and self._daily_token_soft_limit_should_defer("photo_prompt"):
            logger.info("QQ 空间主动配图跳过: token_soft_limit")
            self._qzone_note_publish_image_status(state, reason, "skipped:token_budget", "token 软上限保护")
            return []
        generator = getattr(self, "_generate_photo_image", None)
        if not callable(generator):
            logger.info("QQ 空间主动配图跳过: image_generator_unavailable")
            self._qzone_note_publish_image_status(state, reason, "skipped:no_generator", "缺少 _generate_photo_image 生图入口")
            return []

        style_name, style_instruction = self._get_photo_style_instruction()
        post_text = self._qzone_relationship_safe_source(post_text, source="qzone.image.post_text")
        current_desc = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.image.current_schedule",
        ) or "无明确日程"
        state_desc = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.image.current_state",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.image.recent_diary",
        )
        content_options = ""
        try:
            content_options = self._format_content_choice_options_for_prompt()
        except Exception:
            content_options = "生活小物、窗边光影、路上风景、桌面一角、随手自拍、偶遇小动物。"
        content_options = self._qzone_relationship_safe_source(
            content_options,
            source="qzone.image.content_options",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        qzone_selfie_reference_path = ""
        qzone_selfie_reference_exists = False
        reference_getter = getattr(self, "_photo_persona_reference_image_for_kind_async", None)
        if callable(reference_getter):
            try:
                qzone_selfie_reference_path = await reference_getter(
                    "selfie",
                    allow_daily_outfit=True,
                    request_text=f"说说：{post_text}",
                    ambient_context=f"当前日程：{current_desc}\n当前状态：{state_desc}",
                )
            except Exception as ref_exc:
                logger.info(
                    "QQ 空间自拍参考图预检失败: reason=%s error=%s",
                    _single_line(reason, 40),
                    _single_line(ref_exc, 120),
                )
                qzone_selfie_reference_path = ""
        try:
            qzone_selfie_reference_exists = bool(
                qzone_selfie_reference_path and Path(str(qzone_selfie_reference_path)).exists()
            )
        except (OSError, ValueError):
            qzone_selfie_reference_exists = False
        reference_text = (
            "有可用参考图。可以选择 selfie 让人物自然入镜，但不要默认镜前自拍；只有正文或日程明确需要穿搭/照镜子时才用镜前构图。选择 selfie 时 prompt 必须写明保持参考图中的人物身份、脸部、发色、瞳色、穿搭连续性。"
            if qzone_selfie_reference_path
            else "当前没有可用自拍参考图。可以让人物自然入镜，人物外貌参考人格描述和公开状态；优先使用第一视角手部、侧脸、背影、肩颈半身、影子、随身小物等不强依赖精确脸部的方式，避免凭空追加人格里没有的脸部细节，也不要默认镜前自拍。"
        )
        instruction = """
请为一条即将公开发布到 QQ 空间的说说生成一张配图提示词。
只输出 JSON，不要解释。
""".strip()

        output_contract = f"""输出 JSON：
{{
  "kind": "selfie 或 text2img；按说说正文选择，不要固定优先镜前自拍",
  "visual_anchor": "本图唯一视觉锚点，例如第一视角手部与饮品/桌面小物/路上夕光/侧脸看窗边光影/背影走在路上/餐盘与衣袖；必须具体",
  "composition": "构图一句话，例如第一视角手部近景/桌面俯拍/侧脸三分构图/背影环境中景/路边半身随拍/窗边剪影；镜前自拍只能偶尔使用",
  "prompt": "给生图后端的中文提示词，包含唯一主体、场景、光线、构图、情绪和风格；不要写聊天口吻",
  "caption": "一句画面说明"
}}

要求：
1. 图片必须像公开动态配图，不要包含私聊、系统、插件、模型、内部状态数值。
2. 先确定一个“唯一视觉锚点”，不要把多个主体拼在一张图里；画面要贴合说说正文和当前日程，不要为了配图硬画无关内容。
3. 人物可以入镜，但不要每次都自拍；在第一视角手部、桌面小物、食物饮品、路上光影、窗边侧脸、背影、影子、随身小物和半身随拍之间轮换。
4. 镜前自拍、镜中自拍、手机挡脸自拍不是默认模板；只有正文/日程明确涉及穿搭、整理仪容、出门前照镜子或房间镜子时才使用，且不要连续复用。
5. 如果有自拍参考图，选择 selfie 时必须写清“保留参考图人物身份和外观”“脸部完整清晰”“不要裁脸/遮脸/只拍身体局部”，并让场景来自当前日程；但仍要优先考虑非镜前构图。
6. 如果没有自拍参考图，仍可选择人物入镜；人物外貌以人格描述、公开状态和风格设定为准，不要追加人格里没有的脸部细节。优先使用不强依赖精确脸部的自然入镜方式，比如侧脸、背影、第一视角手部、肩颈半身、窗边剪影。
7. 如果选择 text2img：也可以保留人的存在感，如手边物件、脚步、背影、影子或随身小物；只有画面确实不适合人物入镜时才纯物件/纯风景。
8. 不要包含 NSFW、真实用户隐私、聊天截图或电脑屏幕内容；避免文字、水印、UI、二维码、聊天气泡。
9. prompt 必须体现上面的生图风格要求，且不能是泛泛的“好看的照片/生活记录/天气图”。
""".strip()
        prompt = "\n\n".join(
            part for part in (
                render_prompt_sections([prompt_section(key="qzone.photo_prompt.instruction", title="QQ 空间配图提示词任务", source="qzone_publish", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.photo_prompt.post", title="说说正文", source="qzone_publish", content=_single_line(post_text, 300)),
                        prompt_section(key="qzone.photo_prompt.persona", title="人格", source="qzone_publish", content=self._get_default_persona_prompt()),
                        prompt_section(key="qzone.photo_prompt.state", title="公开可写的状态余味", source="qzone_publish", content=state_desc),
                        prompt_section(key="qzone.photo_prompt.schedule", title="当前/附近日程", source="qzone_publish", content=current_desc),
                        prompt_section(key="qzone.photo_prompt.diary", title="近日日记余味", source="qzone_publish", content=_single_line(diary_context, 500) or "暂无"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                self._format_worldview_adaptation_prompt(),
                relationship_authority_guard,
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.photo_prompt.options", title="可选画面方向", source="qzone_publish", content=content_options),
                        prompt_section(key="qzone.photo_prompt.reference", title="自拍参考图状态", source="qzone_publish", content=reference_text),
                        prompt_section(key="qzone.photo_prompt.style", title="空间配图风格提示", source="qzone_publish", content=self._qzone_publish_image_style_prompt()),
                        prompt_section(key="qzone.photo_prompt.backend_style", title="生图风格", source="qzone_publish", content=f"{style_name}\n风格要求：{style_instruction}"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                render_prompt_sections([prompt_section(key="qzone.photo_prompt.output", title="配图 JSON 输出契约", source="qzone_publish", content=output_contract)], mode=PromptRenderMode.BODY_ONLY),
            ) if part
        )
        try:
            text = await self._llm_call(
                prompt,
                max_tokens=360,
                provider_id=self._task_provider(
                    runtime_persona_setting(self, "PHOTO_PROMPT_PROVIDER_ID", ""),
                    runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                ),
                task=f"qzone_{reason}_photo_prompt",
            )
            payload = self._extract_json_payload(text or "")
            if isinstance(payload, dict):
                workflow_kind = _single_line(payload.get("kind"), 60).lower()
                visual_anchor = _single_line(payload.get("visual_anchor"), 120)
                composition = _single_line(payload.get("composition"), 120)
                image_prompt = _single_line(payload.get("prompt"), 600)
                caption = _single_line(payload.get("caption"), 180)
            else:
                workflow_kind = "text2img"
                visual_anchor = ""
                composition = ""
                image_prompt = _single_line(text, 600)
                caption = image_prompt
            if any(token in workflow_kind for token in ("selfie", "portrait", "自拍", "人像", "人物", "出镜")):
                workflow_kind = "selfie"
            elif any(token in workflow_kind for token in ("text2img", "scene", "photo", "风景", "静物", "物件")):
                workflow_kind = "text2img"
            else:
                workflow_kind = "text2img"
            if not image_prompt:
                image_prompt = f"QQ 空间公开动态配图，{_single_line(post_text, 160)}，{style_instruction}"
            if visual_anchor and visual_anchor not in image_prompt:
                image_prompt = f"唯一视觉锚点：{visual_anchor}。{image_prompt}"
            if composition and composition not in image_prompt:
                image_prompt = f"{image_prompt}。构图：{composition}"
            if workflow_kind == "selfie":
                if qzone_selfie_reference_path:
                    image_prompt = (
                        f"{image_prompt}。保留参考图中的人物身份、脸部、发色、瞳色和穿搭连续性；"
                        "脸部完整清晰，头发、肩颈和上半身自然入镜；不要裁脸、遮脸、背影、只拍身体局部。"
                    )
                else:
                    image_prompt = (
                        f"{image_prompt}。人物是画面主角，外貌参考人格描述、公开状态和风格设定；没有可用自拍参考图时不要追加人格里没有的脸部细节；"
                        "优先使用第一视角手部、侧脸、背影、肩颈半身、窗边剪影、随身小物等自然入镜方式；不要默认镜前自拍或手机挡脸自拍，保持公开动态随手拍质感。"
                    )
            else:
                image_prompt = (
                    f"{image_prompt}。画面像 QQ 空间公开生活配图，单一主体清楚，不出现聊天截图、UI、二维码、水印或虚构人物脸部。"
                )
            reference_image_path = qzone_selfie_reference_path if workflow_kind == "selfie" else ""
            reference_exists = qzone_selfie_reference_exists if workflow_kind == "selfie" else False
            logger.info(
                "QQ 空间配图生图开始: reason=%s kind=%s anchor=%s composition=%s reference=%s reference_exists=%s post=%s prompt=%s",
                _single_line(reason, 40),
                _single_line(workflow_kind, 30),
                _single_line(visual_anchor, 80) or "-",
                _single_line(composition, 80) or "-",
                bool(reference_image_path),
                reference_exists,
                _single_line(post_text, 120),
                _single_line(image_prompt, 180),
            )
            backend_name, image_path, workflow_note = await generator(
                workflow_kind=workflow_kind,
                prompt_text=image_prompt,
                session_key=f"qzone_{reason}",
                reference_image_path=reference_image_path,
            )
        except Exception as exc:
            logger.info("QQ 空间主动配图失败: %s", _single_line(exc, 120))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:prompt_or_generate",
                exc,
                reference_image=qzone_selfie_reference_path,
                reference_exists=qzone_selfie_reference_exists,
            )
            return []
        if not image_path:
            logger.info("QQ 空间主动配图跳过: %s", _single_line(workflow_note, 160))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:no_image",
                workflow_note,
                backend=backend_name,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
            return []
        if not re.match(r"^(?:https?://|file://|data:)", str(image_path), flags=re.I) and not Path(str(image_path)).exists():
            logger.info("QQ 空间主动配图跳过: image_path_missing path=%s", _single_line(image_path, 160))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:path_missing",
                "生图返回路径不存在",
                path=image_path,
                backend=backend_name,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
            return []
        if isinstance(state, dict):
            prefix = self._qzone_reason_prefix(reason)
            state["last_generated_image_path"] = _path_text(image_path, 1000)
            state["last_generated_image_at"] = _now_ts()
            state["last_generated_image_reason"] = reason
            state["last_generated_image_caption"] = _single_line(caption, 180)
            state["last_generated_image_backend"] = _single_line(backend_name, 40)
            if visual_anchor:
                state["last_generated_image_anchor"] = _single_line(visual_anchor, 120)
            if composition:
                state["last_generated_image_composition"] = _single_line(composition, 120)
            if reference_image_path:
                state["last_generated_image_reference"] = _path_text(reference_image_path, 1000)
            state["last_generated_image_reference_exists"] = bool(reference_exists)
            state[f"last_{prefix}_generated_image_path"] = _path_text(image_path, 1000)
            state[f"last_{prefix}_generated_image_at"] = _now_ts()
            state[f"last_{prefix}_generated_image_text"] = _single_line(post_text, 300)
            state[f"last_{prefix}_generated_image_caption"] = _single_line(caption, 180)
            state[f"last_{prefix}_generated_image_backend"] = _single_line(backend_name, 40)
            if visual_anchor:
                state[f"last_{prefix}_generated_image_anchor"] = _single_line(visual_anchor, 120)
            if composition:
                state[f"last_{prefix}_generated_image_composition"] = _single_line(composition, 120)
            self._qzone_note_publish_image_status(
                state,
                reason,
                "generated",
                workflow_note or "ok",
                path=image_path,
                backend=backend_name,
                caption=caption,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
        logger.info(
            "QQ 空间主动配图完成: reason=%s backend=%s reference=%s reference_exists=%s path=%s",
            reason,
            _single_line(backend_name, 40),
            bool(reference_image_path),
            reference_exists,
            _single_line(image_path, 160),
        )
        return [image_path]
