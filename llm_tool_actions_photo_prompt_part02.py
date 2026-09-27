# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoPromptPart02Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_photo_prompt.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 376 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoPromptMixin）。
"""
from __future__ import annotations

from .llm_tool_actions_photo_prompt_shared import (
    _CURRENT_MEDIA_IMAGE_SUFFIXES,
    _CURRENT_MEDIA_MAX_AGE_SECONDS,
    _CURRENT_MEDIA_MAX_BYTES,
    _PHOTO_TOOL_HTTP_URL_RE,
    _PHOTO_TOOL_POSIX_PATH_START_RE,
    _PHOTO_TOOL_REDACTED_LOCAL_PATH,
    _PHOTO_TOOL_RELATIVE_PATH_START_RE,
    _PHOTO_TOOL_WINDOWS_PATH_START_RE,
)
from .llm_tool_actions_photo_prompt_shared import Any
from .llm_tool_actions_photo_prompt_shared import AstrMessageEvent
from .llm_tool_actions_photo_prompt_shared import Path
from .llm_tool_actions_photo_prompt_shared import _redact_outbound_secrets
from .llm_tool_actions_photo_prompt_shared import _render_tool_prompt_section_labeled
from .llm_tool_actions_photo_prompt_shared import _single_line
from .llm_tool_actions_photo_prompt_shared import _strip_internal_message_blocks
from .llm_tool_actions_photo_prompt_shared import get_astrbot_data_path
from .llm_tool_actions_photo_prompt_shared import hashlib
from .llm_tool_actions_photo_prompt_shared import logger
from .llm_tool_actions_photo_prompt_shared import os
from .llm_tool_actions_photo_prompt_shared import re
from .llm_tool_actions_photo_prompt_shared import runtime_persona_setting
from .llm_tool_actions_photo_prompt_shared import shutil
from .llm_tool_actions_photo_prompt_shared import time
from .llm_tool_actions_photo_prompt_shared import uuid



class LlmToolActionsPhotoPromptPart02Mixin:
    """LlmToolActionsPhotoPromptPart02Mixin（从 LlmToolActionsPhotoPromptMixin 拆出）。"""


    def _photo_generation_tool_instruction(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_spontaneous: bool | None = None,
        spontaneous_only: bool = False,
        allow_photo_on_reaction_turns: bool = False,
    ) -> str:
        return _render_tool_prompt_section_labeled(
            self._photo_generation_tool_prompt_section(
                event,
                include_spontaneous=include_spontaneous,
                spontaneous_only=spontaneous_only,
                allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
            ),
        )

    def _photo_tool_followup_is_redundant(self, sent_caption: Any, followup_text: Any) -> bool:
        """Only catch clear repeats of a caption already delivered with the image."""

        def compact(value: Any) -> str:
            text = _strip_internal_message_blocks(str(value or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).lower()
            return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)

        caption = compact(sent_caption)
        followup = compact(followup_text)
        if caption and caption == followup:
            return True
        if len(caption) < 6 or len(followup) < 6:
            return False
        shorter, longer = sorted((caption, followup), key=len)
        return shorter in longer and len(shorter) / max(1, len(longer)) >= 0.45

    def _sanitize_photo_tool_caption(self, value: Any, *, limit: int = 120) -> str:
        """Keep synthesis and internal control cues out of visible image captions."""
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(value, max(1, int(limit or 120)))
        cleaned = _strip_internal_message_blocks(
            str(value or ""),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        cleaned = re.sub(r"&&[A-Za-z_][A-Za-z0-9_ -]{0,31}&&", "", cleaned)
        cue_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if bool(runtime_persona_setting(self, "enable_tts_enhancement", False)) and callable(cue_cleaner):
            cleaned = cue_cleaner(cleaned)
        return _single_line(cleaned, max(1, int(limit or 120)))

    @staticmethod
    def _photo_caption_is_generic(value: Any) -> bool:
        text = re.sub(
            r"[\s。.!！?？,，；;:：、~～…\"'“”‘’（）()【】\[\]]+",
            "",
            str(value or ""),
        ).casefold()
        if not text:
            return True
        polite_tail = r"(?:啦|了|哦|噢|喔|呀|哈|呢)*"
        handoff_tail = (
            r"(?:(?:给|发|送)你(?:看|看看|了)?|"
            r"给你看(?:看)?|请查收)?" + polite_tail
        )
        return any(
            re.fullmatch(pattern, text)
            for pattern in (
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:这张)?"
                rf"(?:图|图片|照片|画面)(?:已经|已|刚刚)?"
                rf"(?:生|生成|画|绘制|改|修改|做|拍|处理|出)?"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:已经|已|刚刚)?"
                rf"(?:生图|出图|生成|画|绘制|改|修改|做好|做|拍|处理)"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:图|图片|照片)?(?:已经|已)?(?:发送|发出|送达)"
                rf"(?:成功|完成|好了?)?{polite_tail}",
                rf"(?:已经|已)?(?:发|发送|送)给你{polite_tail}",
                rf"(?:给|发)(?:你)?(?:看|看看){polite_tail}",
                rf"(?:完成|完成了|好了|成功){polite_tail}",
            )
        )

    @staticmethod
    def _photo_generation_policy_refusal(value: Any) -> bool:
        """Recognize a provider refusal without judging the user's prompt locally."""
        normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        if not normalized:
            return False
        refusal_markers = (
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "request was rejected",
            "request was blocked",
            "内容政策拒绝",
            "内容策略拒绝",
            "安全策略拒绝",
            "请求被安全策略拦截",
        )
        policy_markers = (
            "generative ai prohibited use policy",
            "content policy violation",
            "sensitive words",
            "violates google's",
            "violates the policy",
            "policy violation",
            "不符合内容政策",
            "违反内容政策",
            "敏感词",
        )
        return any(marker in normalized for marker in refusal_markers) and any(
            marker in normalized for marker in policy_markers
        )

    def _sanitize_photo_tool_result_payload(
        self,
        value: Any,
        *,
        known_paths: tuple[Any, ...] = (),
    ) -> Any:
        """Remove local filesystem details from the model-visible tool receipt."""

        absolute_known_paths: list[str] = []
        for candidate in known_paths:
            text = str(candidate or "").strip()
            if not text:
                continue
            if text.lower().startswith(("http://", "https://", "data:")):
                continue
            if (
                _PHOTO_TOOL_WINDOWS_PATH_START_RE.match(text)
                or _PHOTO_TOOL_POSIX_PATH_START_RE.match(text)
                or (len(text) >= 3 and ("/" in text or "\\" in text))
            ):
                absolute_known_paths.append(text)
        absolute_known_paths.sort(key=len, reverse=True)

        def redact_text(raw: Any) -> str:
            cleaned = _redact_outbound_secrets(raw, self)
            protected_urls: dict[str, str] = {}

            def protect_url(match: re.Match[str]) -> str:
                token = f"PCPHOTOURL{uuid.uuid4().hex}TOKEN"
                protected_urls[token] = match.group(0)
                return token

            cleaned = _PHOTO_TOOL_HTTP_URL_RE.sub(protect_url, cleaned)
            for path in absolute_known_paths:
                cleaned = cleaned.replace(path, _PHOTO_TOOL_REDACTED_LOCAL_PATH)
            starts = [
                match.start()
                for pattern in (
                    _PHOTO_TOOL_WINDOWS_PATH_START_RE,
                    _PHOTO_TOOL_POSIX_PATH_START_RE,
                    _PHOTO_TOOL_RELATIVE_PATH_START_RE,
                )
                if (match := pattern.search(cleaned)) is not None
            ]
            if starts:
                prefix = cleaned[: min(starts)].rstrip()
                cleaned = f"{prefix} {_PHOTO_TOOL_REDACTED_LOCAL_PATH}".strip()
            for token, url in protected_urls.items():
                cleaned = cleaned.replace(token, url)
            return cleaned

        sensitive_path_keys = {
            "path",
            "paths",
            "image_path",
            "image_paths",
            "reference_path",
            "reference_paths",
            "reference_image_path",
            "reference_image_paths",
            "resolved_path",
            "prompt_path",
        }

        def sanitize(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned_dict: dict[Any, Any] = {}
                for key, child in item.items():
                    normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
                    if normalized_key in sensitive_path_keys or normalized_key.endswith("_local_path"):
                        continue
                    cleaned_dict[key] = sanitize(child)
                return cleaned_dict
            if isinstance(item, (list, tuple, set)):
                return [sanitize(child) for child in item]
            if isinstance(item, str):
                return redact_text(item)
            return item

        return sanitize(value)

    @staticmethod
    def _current_turn_has_delivered_media(event: AstrMessageEvent) -> bool:
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return True
        chains = getattr(event, "_private_companion_confirmed_send_chains", None)
        if not isinstance(chains, list):
            return False
        for chain in chains:
            if not isinstance(chain, (list, tuple)):
                continue
            for component in chain:
                component_name = type(component).__name__.casefold()
                if component_name in {"image", "file", "video", "record", "audio"}:
                    return True
        return False

    @staticmethod
    def _referenced_media_edit_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:把|将|给|帮我|替我).{0,18}"
                r"(?:改成|变成|换成|调成|染成|改为|变为|换为|调为)"
                r"|(?:改|换|调|染).{0,10}(?:颜色|色调|背景|尺寸|大小|亮度|对比度|饱和度)",
                compact,
                flags=re.I,
            )
        )

    @staticmethod
    def _current_media_private_delivery_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).casefold()
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:私聊|私信|私发|dm).{0,8}(?:发|给|传|丢|送)?(?:给)?我"
                r"|(?:发|给|传|丢|送).{0,8}(?:到|去)?(?:我)?(?:私聊|私信|dm)",
                compact,
                flags=re.I,
            )
        )

    @classmethod
    def _current_media_delivery_instruction_matches(cls, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if cls._current_media_private_delivery_instruction_matches(compact):
            return True
        if re.search(
            r"(?:把|将|给|帮我|麻烦)?(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图|表情包|表情|贴纸|反应图|梗图).{0,8}(?:发|传|给|贴|丢)(?:出来|过来|给我|我)?"
            r"|(?:发|传|给|贴|丢).{0,8}(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图|表情包|表情|贴纸|反应图|梗图)",
            compact,
            flags=re.I,
        ):
            return True
        # Follow-up requests often refer to the failed result indirectly, for
        # example "不要生成新图，把刚刚没发出来的发给我". Keep the
        # delivery tool available when the same sentence still contains an
        # image anchor, a recent-result anchor, and an actual send instruction.
        has_media_anchor = bool(
            re.search(
                r"(?:图|图片|照片|成图|图像|画面|表情包|贴纸|反应图|梗图|这张|那张|这一张|那一张)",
                compact,
                flags=re.I,
            )
        )
        has_recent_anchor = bool(
            re.search(
                r"(?:刚才|刚刚|之前|上次|前面|上面|原来|已有|现成|生成|画好|做好|改好|没发|未发|没送|未送)",
                compact,
                flags=re.I,
            )
        )
        has_delivery_action = bool(
            re.search(
                r"(?:发|传|贴|丢|送)(?:出来|过来|给我|我|一下|一次)?",
                compact,
                flags=re.I,
            )
        )
        return has_media_anchor and has_recent_anchor and has_delivery_action

    def _current_media_allowed_roots(self) -> list[Path]:
        roots: list[Path] = []

        def add(candidate: Any) -> None:
            text = str(candidate or "").strip()
            if not text:
                return
            try:
                resolved = Path(text).expanduser().resolve()
            except Exception:
                return
            if resolved not in roots:
                roots.append(resolved)

        try:
            add(Path(get_astrbot_data_path()) / "temp")
        except Exception:
            pass
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            add(Path(data_dir) / "generated_photos")
        return roots

    @staticmethod
    def _current_media_image_signature_suffix(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                header = handle.read(16)
        except OSError:
            return ""
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        if header.startswith(b"BM"):
            return ".bmp"
        if len(header) >= 12 and header[4:12] in {b"ftypavif", b"ftypavis"}:
            return ".avif"
        return ""

    @classmethod
    def _normalize_current_media_image_suffix(cls, path: Path) -> Path | None:
        actual_suffix = cls._current_media_image_signature_suffix(path)
        if not actual_suffix:
            return None
        current_suffix = path.suffix.casefold()
        if current_suffix == actual_suffix or {
            current_suffix,
            actual_suffix,
        } <= {".jpg", ".jpeg", ".jfif"}:
            return path

        temporary: Path | None = None
        try:
            stat = path.stat()
            fingerprint = hashlib.sha256(
                f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:12]
            normalized = path.with_name(
                f"{path.stem}.pc-media-{fingerprint}{actual_suffix}"
            )
            temporary = normalized.with_name(
                f"{normalized.name}.{uuid.uuid4().hex}.tmp"
            )
            shutil.copyfile(path, temporary)
            os.replace(temporary, normalized)
            return normalized.resolve(strict=True)
        except Exception as exc:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except Exception:
                pass
            logger.warning(
                "当前媒体扩展名规范化失败: file=%s error=%s",
                path.name,
                _single_line(exc, 160),
            )
            return None

    def _resolve_current_media_image(self, value: Any) -> tuple[Path | None, str]:
        raw = str(value or "").strip().strip('"').strip("'")
        if not raw or raw.casefold().startswith(("http://", "https://", "data:", "base64://")):
            return None, "只支持本轮工具返回的本地图片路径"
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except Exception:
            return None, "本轮生成的图片文件不存在"
        if not path.is_file() or path.suffix.casefold() not in _CURRENT_MEDIA_IMAGE_SUFFIXES:
            return None, "只允许发送本轮生成的常见图片文件"
        if not any(path.is_relative_to(root) for root in self._current_media_allowed_roots()):
            return None, "图片不在允许的 AstrBot 临时目录或本插件成图目录内"
        try:
            stat = path.stat()
        except OSError:
            return None, "无法读取本轮生成的图片文件"
        if stat.st_size <= 0 or stat.st_size > _CURRENT_MEDIA_MAX_BYTES:
            return None, "图片为空或超过 32 MB 发送上限"
        age = time.time() - float(stat.st_mtime or 0)
        if age < -60 or age > _CURRENT_MEDIA_MAX_AGE_SECONDS:
            return None, "图片不是本轮近期生成的文件"
        normalized_path = self._normalize_current_media_image_suffix(path)
        if normalized_path is None:
            return None, "文件内容不是支持的实际图片格式"
        return normalized_path, ""
