# -*- coding: utf-8 -*-
"""ForwardMessageReplyRichCardMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 245 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

import html
import json
import re
from .forward_message_shared import _render_conversation_section_labeled, logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
from urllib.parse import urlparse
from .forward_message_shared import PromptSection
from .forward_message_shared import _single_line
from .forward_message_shared import prompt_section



class ForwardMessageReplyRichCardMixin:
    """ForwardMessageReplyRichCardMixin（从 ForwardMessageMixin 拆出）。"""


    def _extract_reply_rich_card_info(self, message_obj: Any) -> dict[str, Any]:
        texts: list[str] = []
        links: list[str] = []
        images: list[str] = []
        seen_values: set[str] = set()

        def normalize_card_text(value: Any) -> str:
            text = html.unescape(str(value or "")).strip()
            text = text.replace("\\/", "/")
            text = text.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u003f", "?")
            text = text.replace("\\\\", "\\")
            return text

        def looks_like_image_source(value: str) -> bool:
            text = normalize_card_text(value)
            if not text:
                return False
            if text.startswith(("http://", "https://", "file://", "data:")):
                lowered = text.lower()
                if re.search(r"\.(?:png|jpe?g|gif|webp|bmp)(?:[?#].*)?$", text, re.I) or "/bfs/" in text or "image" in lowered:
                    return True
                # QQ 图床下载直链不带扩展名；同域名也承载音频，需要按格式排除。
                hostname = (urlparse(text).hostname or "").lower().rstrip(".")
                if hostname in {"multimedia.nt.qq.com", "multimedia.nt.qq.com.cn", "qpic.cn"} or hostname.endswith(".qpic.cn"):
                    return not re.search(r"[?&]format=(?:amr|silk|mp3|m4a|wav|ogg|mp4)\b", lowered)
                return False
            return bool(
                re.search(r"(?:^|[A-Za-z]:\\|/).+\.(?:png|jpe?g|gif|webp|bmp)$", text, re.I)
            )

        def add_text(value: Any) -> None:
            text = _single_line(normalize_card_text(value), 160)
            if text and text not in seen_values and not text.startswith(("http://", "https://")):
                seen_values.add(text)
                texts.append(text)

        def add_link(value: Any) -> None:
            text = normalize_card_text(value)
            if text and text.startswith(("http://", "https://")) and text not in links:
                links.append(text)

        def add_image(value: Any) -> None:
            text = normalize_card_text(value)
            if text and looks_like_image_source(text) and text not in images:
                images.append(text)

        def visit(value: Any, *, key_hint: str = "", depth: int = 0) -> None:
            if value is None or depth > 8:
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key_hint=key_hint, depth=depth + 1)
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key or "").lower()
                    if isinstance(child, str):
                        if any(token in key_text for token in ("image", "img", "pic", "picture", "cover", "preview", "thumb", "icon", "file", "path")):
                            add_image(child)
                        if any(token in key_text for token in ("url", "jump", "link", "uri")):
                            add_link(child)
                        if any(token in key_text for token in ("title", "desc", "summary", "content", "text", "prompt", "tag")):
                            add_text(child)
                    visit(child, key_hint=key_text, depth=depth + 1)
                return
            raw = str(value or "").strip()
            if not raw:
                return
            unescaped = normalize_card_text(raw)
            compact = unescaped.strip()
            if compact.startswith(("{", "[")):
                try:
                    visit(json.loads(compact), key_hint=key_hint, depth=depth + 1)
                    return
                except Exception:
                    pass
            quoted_json = compact
            if (quoted_json.startswith('"') and quoted_json.endswith('"')) or (quoted_json.startswith("'") and quoted_json.endswith("'")):
                try:
                    visit(json.loads(quoted_json), key_hint=key_hint, depth=depth + 1)
                    return
                except Exception:
                    pass
            for url in re.findall(r"https?://[^\s\"'<>\\)）]+", unescaped):
                add_link(url)
                if looks_like_image_source(url):
                    add_image(url)
            for file_path in re.findall(r"[A-Za-z]:\\[^\s\"'<>|]+?\.(?:png|jpe?g|gif|webp|bmp)", unescaped, re.I):
                add_image(file_path)
            for file_uri in re.findall(r"file://[^\s\"'<>]+?\.(?:png|jpe?g|gif|webp|bmp)", unescaped, re.I):
                add_image(file_uri)
            for cq_match in re.finditer(r"\[CQ:image,([^\]]+)\]", unescaped):
                fields: dict[str, str] = {}
                for part in cq_match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = normalize_card_text(val)
                for key in ("url", "file", "path"):
                    candidate = fields.get(key)
                    if candidate:
                        if looks_like_image_source(candidate):
                            add_image(candidate)
                        elif candidate.startswith(("http://", "https://")):
                            add_link(candidate)
            for image_match in re.findall(
                r'"(?:image|img|pic|picture|cover|preview|thumb|icon|src|url|file|path)"\s*:\s*"([^"]+)"',
                unescaped,
                re.I,
            ):
                normalized_image = normalize_card_text(image_match)
                if looks_like_image_source(normalized_image):
                    add_image(normalized_image)
                elif normalized_image.startswith(("http://", "https://")):
                    add_link(normalized_image)
            if key_hint in {"data"} and ("bilibili" in unescaped or "哔哩" in unescaped or "明日方舟" in unescaped):
                for text_match in re.findall(r'"(?:title|desc|summary|content|text|prompt)"\s*:\s*"([^"]{2,160})"', unescaped):
                    add_text(text_match)

        visit(message_obj)
        return {"texts": texts[:8], "links": links[:8], "images": images[:6]}

    @staticmethod
    def _reply_rich_card_music_album_context(texts: list[str], links: list[str]) -> dict[str, str]:
        joined = " ".join([*texts, *links])
        compact = re.sub(r"\s+", "", joined)
        if not compact:
            return {}
        if not any(token in compact for token in ("网易云音乐", "专辑", "歌手", "歌曲", "曲目", "歌单", "music.163.com")):
            return {}
        album = ""
        artist = ""
        platform = ""
        for text in texts:
            normalized = _single_line(text, 120)
            if not album:
                match = re.search(r"(?:专辑|album)\s*[：:]\s*([^\s，。！？!?]{2,60})", normalized, re.I)
                if match:
                    album = _single_line(match.group(1), 60)
            if not artist:
                match = re.search(r"(?:歌手|artist)\s*[：:]\s*([^\s，。！？!?]{2,40})", normalized, re.I)
                if match:
                    artist = _single_line(match.group(1), 40)
            if not platform and any(token in normalized for token in ("网易云音乐", "music.163.com")):
                platform = "网易云音乐"
        if not album and not artist and not platform:
            return {}
        return {
            "album": album,
            "artist": artist,
            "platform": platform,
            "summary": "；".join(
                part for part in (
                    f"专辑：{album}" if album else "",
                    f"歌手：{artist}" if artist else "",
                    platform or "",
                )
                if part
            ),
        }

    async def _format_reply_rich_card_context_prompt_section(
        self,
        event: AstrMessageEvent,
    ) -> PromptSection | None:
        message_id, raw_message = await self._reply_raw_message_for_event(event)
        if raw_message is None:
            return None
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
        if recalled_message_id:
            logger.info("引用卡片上下文跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
            return None
        info = self._extract_reply_rich_card_info(raw_message)
        texts = [item for item in info.get("texts", []) if item]
        links = [item for item in info.get("links", []) if item]
        images = [item for item in info.get("images", []) if item]
        if not (texts or links or images):
            return None
        music_context = self._reply_rich_card_music_album_context(texts, links)
        image_vision_text = await self._transcribe_forward_message_images(event, images)
        lines = [
            "这轮用户引用了一条卡片/动态，内容如下：",
        ]
        lines.extend(self._reply_actor_binding_prompt_lines())
        if message_id:
            lines.append(f"引用消息ID：{message_id}")
        if texts:
            lines.append("卡片文字：" + "；".join(texts[:5]))
            compact_text = re.sub(r"\s+", "", " ".join(texts))
            if "最近撤回消息" in compact_text and "撤回" in compact_text:
                recalled_rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=5)
                status_parts = [self._recall_image_status_summary(row) for row in recalled_rows]
                status_text = "；".join(part for part in status_parts if part)
                if status_text:
                    lines.append(
                        f"引用内容是插件的撤回查询摘要，不是原始撤回消息本体；当前短期缓存图片状态：{status_text}。"
                        "不要把摘要里的[图片]当成已看见原图。"
                    )
                else:
                    lines.append("引用内容是插件的撤回查询摘要，不是原始撤回消息本体；摘要里的[图片]只表示对方撤回过图片。")
        if links:
            lines.append("卡片链接：" + "；".join(links[:4]))
        if images:
            lines.append(f"卡片图片数：{len(images)}")
        if music_context:
            lines.append("音乐卡片识别：")
            lines.append(
                "这是一张音乐专辑/点歌卡片，卡片里的专辑名和歌手名应当直接视为有效线索；"
                "如果用户是在让你发出这张专辑的几首歌，不要再追问“哪个专辑”，优先按卡片里的信息继续。"
            )
            lines.append(f"音乐卡片摘要：{music_context.get('summary') or '（未提取到完整信息）'}")
            try:
                setattr(event, "private_companion_reply_music_album_context", music_context)
            except Exception:
                pass
        if image_vision_text:
            lines.append("引用卡片中的图片：")
            lines.append(image_vision_text)
            logger.info(
                "引用卡片图片视觉摘要完成: message_id=%s images=%s preview=%s",
                message_id or "-",
                len(images),
                _single_line(image_vision_text, 240),
            )
        logger.info(
            "已注入引用卡片上下文: message_id=%s texts=%s links=%s images=%s vision=%s preview=%s vision_preview=%s",
            message_id or "-",
            len(texts),
            len(links),
            len(images),
            bool(image_vision_text),
            _single_line(" | ".join([*texts[:3], *links[:2]]), 240),
            _single_line(image_vision_text, 240) if image_vision_text else "-",
        )
        return prompt_section(
            key="forward.rich_card",
            title="本轮引用卡片/动态",
            source="forward_message",
            content="\n".join(lines),
        )

    async def _format_reply_rich_card_context_for_prompt(
        self,
        event: AstrMessageEvent,
    ) -> str:
        return _render_conversation_section_labeled(
            await self._format_reply_rich_card_context_prompt_section(event)
        )
