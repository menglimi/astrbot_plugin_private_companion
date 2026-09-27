# -*- coding: utf-8 -*-
"""EventDispatchRecallCacheMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 878 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import asyncio
import base64
import re
import shutil
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .event_dispatch_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from collections.abc import Mapping
from pathlib import Path
from typing import Any
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



class EventDispatchRecallCacheMixin:
    """EventDispatchRecallCacheMixin（从 EventDispatchMixin 拆出）。"""


    def _segmented_remainder_lock(self, scope: str) -> asyncio.Lock:
        key = _single_line(scope, 160) or "unknown"
        locks = getattr(self, "_segmented_reply_remainder_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._segmented_reply_remainder_locks = locks
        lock = locks.get(key)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[key] = lock
        if len(locks) > 500:
            for stale_key, stale_lock in list(locks.items()):
                if stale_key != key and isinstance(stale_lock, asyncio.Lock) and not stale_lock.locked():
                    locks.pop(stale_key, None)
                    if len(locks) <= 500:
                        break
        return lock

    def _recall_component_text(self, component: Any) -> str:
        for attr in ("text", "content", "message"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                return value
        if isinstance(component, dict):
            data = component.get("data") if isinstance(component.get("data"), dict) else component
            text = data.get("text") or data.get("content") or data.get("message")
            if text is not None:
                return str(text)
        return ""

    def _event_text_for_recall_cache(self, event: AstrMessageEvent, *, limit: int = 500) -> str:
        parts: list[str] = []
        try:
            chain = list(event.get_messages() or [])
        except Exception:
            chain = []
        for comp in chain:
            text = self._recall_component_text(comp)
            if text:
                parts.append(text)
                continue
            name = comp.__class__.__name__.lower()
            if "image" in name:
                parts.append("[图片]")
            elif "record" in name or "voice" in name:
                parts.append("[语音]")
            elif "video" in name:
                parts.append("[视频]")
            elif "at" == name or name.endswith(".at"):
                parts.append("[@]")
            elif "reply" in name:
                parts.append("[引用]")
        text = " ".join(item.strip() for item in parts if str(item or "").strip()).strip()
        if not text:
            raw = self._event_raw_payload(event)
            raw_msg = raw.get("raw_message") or raw.get("message")
            if isinstance(raw_msg, str):
                text = raw_msg
            else:
                text = str(getattr(event, "message_str", "") or "")
        return _single_line(text, limit)

    async def _event_image_sources_for_recall_cache(self, event: AstrMessageEvent, *, limit: int = 5) -> list[dict[str, str]]:
        scope = re.sub(r"[^0-9A-Za-z_.-]+", "_", self._event_scope_key(event) or "unknown")
        target_dir = Path(self.data_dir) / "recall_message_images" / scope
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        raw_sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in raw_sources:
                raw_sources.append(text)

        async def resolve_component_source(comp: Any) -> str:
            source = ""
            extractor = getattr(self, "_image_component_source", None)
            if callable(extractor):
                try:
                    source = _single_line(extractor(comp), 1000)
                except Exception:
                    source = ""
            local_path_getter = getattr(self, "_private_image_local_path_from_source", None)
            local_path = local_path_getter(source) if source and callable(local_path_getter) else None
            source_is_resolved = bool(
                source.startswith(("http://", "https://", "data:", "base64://"))
                or (local_path is not None and local_path.exists() and local_path.is_file())
            )
            if source_is_resolved:
                return source
            converter = getattr(comp, "convert_to_file_path", None)
            if callable(converter):
                try:
                    maybe = converter()
                    converted = str(await maybe if hasattr(maybe, "__await__") else maybe or "").strip()
                    if converted:
                        return converted
                except Exception as exc:
                    logger.debug("撤回图片组件转换失败: %s", exc)
            return source

        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name != "image":
                continue
            add(await resolve_component_source(comp))

        message_obj = getattr(event, "message_obj", None)
        extractor = getattr(self, "_extract_image_sources_from_message_obj", None)
        if callable(extractor):
            for source in extractor(message_obj):
                add(source)
        raw_extractor = getattr(self, "_raw_private_image_sources", None)
        if callable(raw_extractor):
            for source in raw_extractor(event):
                add(source)

        persisted: list[dict[str, str]] = []
        now_ms = int(_now_ts() * 1000)

        def add_persisted(source: str, tier: str) -> None:
            text = str(source or "").strip()
            tier = _single_line(tier, 40)
            if not text and tier != "placeholder":
                return
            if any(item.get("source") == text and item.get("tier") == tier for item in persisted):
                return
            persisted.append({"source": text, "tier": tier})

        def persist_data_url(source: str, stem: str) -> str:
            text = str(source or "").strip()
            try:
                if text.startswith("base64://"):
                    raw = base64.b64decode(text[len("base64://"):], validate=False)
                    suffix = ".jpg"
                elif text.startswith("data:") and "," in text:
                    meta, payload = text.split(",", 1)
                    if ";base64" not in meta.lower():
                        return ""
                    raw = base64.b64decode(payload, validate=False)
                    lowered = meta.lower()
                    suffix = ".png" if "png" in lowered else ".webp" if "webp" in lowered else ".gif" if "gif" in lowered else ".jpg"
                else:
                    return ""
                if not raw:
                    return ""
                target = target_dir / f"{stem}{suffix}"
                target.write_bytes(raw)
                return str(target)
            except Exception as exc:
                logger.debug("撤回图片 data url 暂存失败: %s", exc)
                return ""

        for index, source in enumerate(raw_sources[: max(1, limit)], 1):
            text = str(source or "").strip()
            if not text:
                continue
            data_path = persist_data_url(text, f"{now_ms}_{index}")
            if data_path:
                add_persisted(data_path, "local")
                continue
            if re.match(r"^https?://", text, flags=re.I):
                downloader = getattr(self, "_persist_private_remote_image_source", None)
                if callable(downloader):
                    try:
                        downloaded = await asyncio.wait_for(downloader(text, target_dir, f"{now_ms}_{index}"), timeout=8.0)
                    except Exception as exc:
                        logger.debug("撤回图片远程暂存失败: %s", exc)
                        downloaded = ""
                    if downloaded:
                        add_persisted(downloaded, "local")
                        continue
                add_persisted(text, "url")
                continue
            local_path_getter = getattr(self, "_private_image_local_path_from_source", None)
            try:
                source_path = local_path_getter(text) if callable(local_path_getter) else Path(text)
                exists = source_path is not None and source_path.exists() and source_path.is_file()
            except (OSError, ValueError):
                exists = False
            if exists:
                suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                target = target_dir / f"{now_ms}_{index}{suffix}"
                try:
                    shutil.copy2(source_path, target)
                    add_persisted(str(target), "local")
                    continue
                except Exception as exc:
                    logger.debug("撤回图片本地暂存失败: %s", exc)
            if not re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\|file://)", text):
                # OneBot/NapCat may expose only a platform-side image file id.
                # Keep it as a best-effort sendable Image(file=...) reference.
                add_persisted(text, "platform_file")
        return persisted[: max(1, limit)]

    def _recall_image_items_from_snapshot(self, row: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        raw_items = row.get("image_items") if isinstance(row.get("image_items"), list) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            tier = _single_line(item.get("tier"), 40) or ("local" if source else "placeholder")
            items.append({"source": source, "tier": tier})
        if items:
            return items
        for source in row.get("images") if isinstance(row.get("images"), list) else []:
            text = str(source or "").strip()
            if not text:
                continue
            tier = "url" if re.match(r"^https?://", text, flags=re.I) else "local"
            if not re.match(r"^https?://", text, flags=re.I) and not text.startswith(("data:", "base64://", "file://")):
                try:
                    path = Path(text)
                    if not (path.exists() and path.is_file()):
                        tier = "platform_file"
                except (OSError, ValueError):
                    tier = "platform_file"
            items.append({"source": text, "tier": tier})
        return items

    def _recall_image_status_summary(self, row: dict[str, Any]) -> str:
        items = self._recall_image_items_from_snapshot(row)
        if not items:
            if "[图片]" in str(row.get("text") or ""):
                return "只有图片占位"
            return ""
        counts = {"local": 0, "platform_file": 0, "url": 0, "placeholder": 0}
        for item in items:
            tier = item.get("tier") or "placeholder"
            counts[tier if tier in counts else "placeholder"] += 1
        parts: list[str] = []
        if counts["local"]:
            parts.append(f"原图已缓存 {counts['local']} 张")
        if counts["platform_file"]:
            parts.append(f"仅有平台 file id {counts['platform_file']} 张")
        if counts["url"]:
            parts.append(f"仅有 URL {counts['url']} 张")
        if counts["placeholder"]:
            parts.append(f"只有图片占位 {counts['placeholder']} 张")
        return "；".join(parts)

    def _cleanup_recall_message_cache(self) -> None:
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            self._recall_message_cache = {}
            self._cleanup_recall_message_image_cache()
            return
        now = _now_ts()
        ttl = max(60.0, _safe_float(_persona_value(self, 'recall_message_cache_ttl_seconds', 600), 600))
        stale = [key for key, item in cache.items() if now - _safe_float(item.get("ts") if isinstance(item, dict) else 0, 0) > ttl]
        for key in stale:
            cache.pop(key, None)
        max_items = max(0, _safe_int(_persona_value(self, 'recall_message_cache_max_items', 300), 300, 0))
        if max_items and len(cache) > max_items:
            ordered = sorted(cache.items(), key=lambda kv: _safe_float(kv[1].get("ts") if isinstance(kv[1], dict) else 0, 0))
            for key, _ in ordered[: len(cache) - max_items]:
                cache.pop(key, None)
        self._cleanup_recall_message_image_cache()

    def _lightweight_recall_message_payload(self, value: Any, *, max_chars: int = 12000) -> Any:
        """Create a bounded, detached payload for the recall cache.

        Platform events may contain nested media metadata or encoded image data.  The
        recall flow only needs message structure and small identifiers, so retaining
        the original object here can keep a surprisingly large event graph alive.
        """
        budget = [max(1024, int(max_chars))]
        preferred_keys = {
            "type", "data", "text", "content", "message", "raw_message", "messages",
            "id", "message_id", "msg_id", "file", "file_id", "url", "path", "name",
            "user_id", "qq", "sender", "nickname", "card", "node", "seq", "time",
        }

        def copy_value(item: Any, depth: int = 0) -> Any:
            if budget[0] <= 0 or depth > 5:
                return "[已省略]"
            if item is None or isinstance(item, (bool, int, float)):
                return item
            if isinstance(item, str):
                value_text = item
                if len(value_text) > budget[0]:
                    value_text = value_text[: max(0, budget[0] - 16)] + "...[已截断]"
                budget[0] -= len(value_text)
                return value_text
            if isinstance(item, Mapping):
                result: dict[str, Any] = {}
                for key, nested in item.items():
                    key_text = str(key)
                    if key_text not in preferred_keys and depth >= 2:
                        continue
                    if budget[0] <= 0:
                        break
                    result[key_text] = copy_value(nested, depth + 1)
                return result
            if isinstance(item, (list, tuple)):
                # Message chains are normally short; cap pathological platform payloads.
                return [copy_value(nested, depth + 1) for nested in list(item)[:32]]
            return copy_value(_single_line(item, 2000), depth + 1)

        return copy_value(value)

    def _cleanup_recall_message_image_cache(self, *, force: bool = False) -> bool:
        root = Path(getattr(self, "data_dir", "") or "") / "recall_message_images"
        try:
            root_resolved = root.resolve()
            data_resolved = Path(getattr(self, "data_dir", "") or "").resolve()
        except Exception:
            return False
        if not root_resolved.exists() or not root_resolved.is_dir():
            return False
        try:
            root_resolved.relative_to(data_resolved)
        except ValueError:
            return False
        now = _now_ts()
        if not force:
            last_cleanup = _safe_float(getattr(self, "_last_recall_image_cache_cleanup_ts", 0), 0)
            if now - last_cleanup < 300:
                return False
        self._last_recall_image_cache_cleanup_ts = now
        ttl = max(60.0, _safe_float(_persona_value(self, 'recall_message_cache_ttl_seconds', 600), 600))
        max_mb = max(0.0, _safe_float(_persona_value(self, 'recall_message_image_cache_max_mb', 256.0), 256.0, 0.0))
        max_bytes = int(max_mb * 1024 * 1024) if max_mb > 0 else 0
        files: list[tuple[float, int, Path]] = []
        removed_count = 0
        removed_bytes = 0

        try:
            iterator = list(root_resolved.rglob("*"))
        except Exception as exc:
            logger.debug("撤回图片缓存扫描失败: %s", exc)
            return False

        for path in iterator:
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                mtime = float(stat.st_mtime or 0)
                size = int(stat.st_size or 0)
                if now - mtime > ttl:
                    try:
                        path.unlink()
                        removed_count += 1
                        removed_bytes += size
                    except Exception as exc:
                        logger.debug("撤回图片过期缓存删除失败: path=%s error=%s", path, exc)
                    continue
                files.append((mtime, size, path))
            except Exception as exc:
                logger.debug("撤回图片缓存条目读取失败: path=%s error=%s", path, exc)

        total_bytes = sum(size for _, size, _ in files)
        if max_bytes and total_bytes > max_bytes:
            for _, size, path in sorted(files, key=lambda item: item[0]):
                if total_bytes <= max_bytes:
                    break
                try:
                    path.unlink()
                    total_bytes -= size
                    removed_count += 1
                    removed_bytes += size
                except Exception as exc:
                    logger.debug("撤回图片容量缓存删除失败: path=%s error=%s", path, exc)

        for directory in sorted((p for p in iterator if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
            except Exception as exc:
                logger.debug("撤回图片空目录清理失败: path=%s error=%s", directory, exc)

        if removed_count:
            logger.info(
                "已清理撤回图片缓存: files=%s size=%.1fMB ttl=%.0fs max=%.1fMB",
                removed_count,
                removed_bytes / 1024 / 1024,
                ttl,
                max_mb,
            )
            return True
        return False

    async def _cache_message_for_recall(self, event: AstrMessageEvent) -> None:
        delivery_cleanup = getattr(self, "_cleanup_framework_delivery_caches", None)
        if callable(delivery_cleanup):
            delivery_cleanup()
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return
        if not _persona_value(self, 'enable_recall_message_cache', True):
            return
        if not self._event_is_platform_message_event(event):
            return
        raw = self._event_raw_payload(event)
        message_ids = self._event_message_id_candidates(event)
        if not message_ids:
            return
        text = self._event_text_for_recall_cache(event, limit=max(80, _safe_int(_persona_value(self, 'recall_message_cache_text_chars', 500), 500, 80)))
        if not text:
            return
        raw_message = str(raw.get("raw_message") or raw.get("message") or "")
        reply_message_ids = self._event_reply_message_ids(event)
        has_image = "[图片]" in text or "[CQ:image" in raw_message
        if not has_image:
            for comp in self._event_components(event):
                class_name = comp.__class__.__name__.lower()
                if isinstance(comp, dict):
                    class_name = str(comp.get("type") or "").lower()
                if class_name == "image":
                    has_image = True
                    break
        image_items = await self._event_image_sources_for_recall_cache(event, limit=5) if has_image else []
        if has_image and not image_items:
            image_items = [{"source": "", "tier": "placeholder"}]
        image_sources = [item.get("source", "") for item in image_items if isinstance(item, dict) and item.get("source")]
        is_message_sent_event = str(raw.get("post_type") or "").strip().lower() == "message_sent"
        snapshot_sender_id = self._event_self_id(event) if is_message_sent_event else self._event_sender_id(event)
        snapshot_sender_name = _single_line(self._sender_display_name(event), 60)
        if is_message_sent_event:
            setting_getter = getattr(self, "persona_setting", None)
            bot_name = setting_getter("bot_name", "") if callable(setting_getter) else _persona_value(self, 'bot_name', "")
            snapshot_sender_name = _single_line(bot_name, 60) or snapshot_sender_name
        tts_spoken_text = ""
        tts_source_text = ""
        is_self_message = bool(
            is_message_sent_event
            or (
                self._event_sender_id(event)
                and self._event_self_id(event)
                and self._event_sender_id(event) == self._event_self_id(event)
            )
        )
        if is_self_message:
            tts_lookup = getattr(self, "_lookup_tts_record_text", None)
            for comp in self._event_components(event):
                class_name = comp.__class__.__name__.lower()
                if isinstance(comp, dict):
                    class_name = str(comp.get("type") or "").strip().lower()
                if class_name not in {"record", "voice", "audio", "voice_message"}:
                    continue
                tts_spoken_text = _single_line(
                    getattr(comp, "_private_companion_tts_spoken_text", ""),
                    500,
                )
                tts_source_text = _single_line(
                    getattr(comp, "_private_companion_tts_source_text", ""),
                    500,
                )
                if not tts_spoken_text and callable(tts_lookup):
                    try:
                        tts_spoken_text, tts_source_text = tts_lookup(comp)
                    except Exception:
                        tts_spoken_text, tts_source_text = "", ""
                if tts_spoken_text:
                    break
            if not tts_spoken_text:
                voice_text_getter = getattr(self, "_message_obj_known_tts_voice_text", None)
                if callable(voice_text_getter):
                    try:
                        tts_spoken_text, tts_source_text = voice_text_getter(
                            raw.get("message") if raw.get("message") is not None else raw.get("raw_message")
                        )
                    except Exception:
                        tts_spoken_text, tts_source_text = "", ""
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recall_message_cache = cache
        self._cleanup_recall_message_cache()
        message_id = message_ids[0]
        raw_payload = raw.get("message") if raw.get("message") is not None else raw.get("raw_message")
        snapshot = {
            "message_id": message_id,
            "message_id_aliases": message_ids,
            "ts": _now_ts(),
            "scope": self._event_scope_key(event),
            "sender_id": snapshot_sender_id,
            "sender_name": snapshot_sender_name,
            "text": text,
            "raw_message": self._lightweight_recall_message_payload(raw_payload),
            "reply_message_ids": reply_message_ids,
            "images": image_sources,
            "image_items": image_items,
            "image_count": len(image_items),
            "tts_spoken_text": tts_spoken_text,
            "tts_source_text": tts_source_text,
        }
        for candidate in message_ids:
            cache[candidate] = snapshot

    def _cleanup_recalled_message_ids(self) -> None:
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            self._recalled_message_ids = {}
            return
        now = _now_ts()
        ttl = max(60.0, _safe_float(getattr(self, "recall_cancel_reply_ttl_seconds", 600), 600))
        stale = [key for key, item in recalled.items() if now - _safe_float(item.get("ts") if isinstance(item, dict) else 0, 0) > ttl]
        for key in stale:
            recalled.pop(key, None)

    def _record_recalled_message_id(
        self,
        message_id: str,
        *,
        scope: str = "",
        notice_type: str = "",
        sender_id: str = "",
    ) -> None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            recalled = {}
            self._recalled_message_ids = recalled
        self._cleanup_recalled_message_ids()
        cache = getattr(self, "_recall_message_cache", None)
        snapshot = dict(cache.get(message_id) or {}) if isinstance(cache, dict) and isinstance(cache.get(message_id), dict) else {}
        if not snapshot:
            snapshot = {
                "message_id": message_id,
                "message_id_aliases": [message_id],
                "ts": _now_ts(),
                "scope": _single_line(scope, 160),
                "sender_id": _single_line(sender_id, 80),
                "sender_name": "",
                "text": "[内容未进入短期缓存]",
                "images": [],
                "image_items": [],
                "image_count": 0,
                "cache_miss": True,
            }
            logger.info(
                "撤回消息快照未命中: scope=%s message_id=%s notice=%s",
                _single_line(scope, 160) or "-",
                message_id,
                _single_line(notice_type, 40) or "-",
            )
        aliases = [message_id]
        if isinstance(snapshot.get("message_id_aliases"), list):
            aliases.extend(_single_line(item, 120) for item in snapshot.get("message_id_aliases") or [])
        record = {
            "ts": _now_ts(),
            "scope": _single_line(scope, 160) or _single_line(snapshot.get("scope"), 160),
            "notice_type": _single_line(notice_type, 40),
            "message": snapshot,
        }
        seen: set[str] = set()
        for candidate in aliases:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            recalled[candidate] = record

    def _is_message_id_recalled(self, message_id: str) -> bool:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return False
        self._cleanup_recalled_message_ids()
        recalled = getattr(self, "_recalled_message_ids", None)
        return isinstance(recalled, dict) and message_id in recalled

    def _reply_cancel_trigger_message_ids(self, event: AstrMessageEvent, *extra_message_ids: str) -> list[str]:
        ids: list[str] = []
        if self._event_is_platform_message_event(event):
            current_id = self._event_message_id(event)
            if current_id:
                ids.append(current_id)
            quote_id = self._group_current_reply_quote_message_id(event)
            if quote_id:
                ids.append(quote_id)
            for message_id in self._event_reply_message_ids(event):
                if message_id:
                    ids.append(message_id)
        for message_id in extra_message_ids:
            message_id = _single_line(message_id, 120)
            if message_id:
                ids.append(message_id)
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        cache = getattr(self, "_recall_message_cache", None)
        if isinstance(cache, dict):
            for message_id in list(unique):
                snapshot = cache.get(message_id)
                nested_ids = snapshot.get("reply_message_ids") if isinstance(snapshot, dict) else None
                if not isinstance(nested_ids, list):
                    continue
                for nested_id in nested_ids:
                    nested = _single_line(nested_id, 120)
                    if nested and nested not in seen:
                        seen.add(nested)
                        unique.append(nested)
        return unique

    def _event_reply_message_ids(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        extractor = getattr(self, "_extract_reply_message_id", None)
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = ""
            if callable(extractor):
                try:
                    message_id = _single_line(extractor(item), 120)
                except Exception:
                    message_id = ""
            if not message_id:
                data = self._component_data(item)
                for key in ("id", "message_id", "msg_id", "seq", "message_seq", "real_id"):
                    value = data.get(key) if isinstance(data, dict) else None
                    if value is not None and str(value).strip():
                        message_id = _single_line(value, 120)
                        break
            if message_id:
                ids.append(message_id)
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        return unique

    def _should_cancel_reply_for_recalled_trigger(self, event: AstrMessageEvent, *extra_message_ids: str) -> str:
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return ""
        if not _persona_value(self, 'enable_recall_cancel_reply', True):
            return ""
        for message_id in self._reply_cancel_trigger_message_ids(event, *extra_message_ids):
            if self._is_message_id_recalled(message_id):
                return message_id
        return ""

    async def _platform_message_exists_for_cancel_check(self, event: AstrMessageEvent, message_id: str) -> bool | None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return None
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        attempts: list[Any] = [message_id]
        try:
            attempts.insert(0, int(message_id))
        except (TypeError, ValueError):
            pass
        for value in attempts:
            try:
                raw = await call_action("get_msg", message_id=value)
            except Exception as exc:
                logger.debug(
                    "触发消息存在性检查失败: message_id=%s error=%s",
                    message_id,
                    _single_line(exc, 120),
                )
                continue
            if raw:
                return True
        # Adapters differ in whether get_msg can read an inbound private message.
        # An unavailable lookup is not proof of a recall; explicit recall notices
        # are recorded in _recalled_message_ids and handled before this fallback.
        return None

    async def _should_cancel_reply_for_missing_or_recalled_trigger(self, event: AstrMessageEvent, *extra_message_ids: str) -> str:
        recalled_message_id = self._should_cancel_reply_for_recalled_trigger(event, *extra_message_ids)
        if recalled_message_id:
            return recalled_message_id
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return ""
        if not _persona_value(self, 'enable_recall_cancel_reply', True):
            return ""
        for message_id in self._reply_cancel_trigger_message_ids(event, *extra_message_ids):
            exists = await self._platform_message_exists_for_cancel_check(event, message_id)
            if exists is False:
                self._record_recalled_message_id(message_id, scope=self._event_scope_key(event), notice_type="missing_before_send")
                return message_id
        return ""

    def _should_cancel_reply_for_recalled_message_ids(self, *message_ids: str) -> str:
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return ""
        if not _persona_value(self, 'enable_recall_cancel_reply', True):
            return ""
        for message_id in message_ids:
            message_id = _single_line(message_id, 120)
            if message_id and self._is_message_id_recalled(message_id):
                return message_id
        return ""

    def _recent_recalled_messages_for_scope(self, scope: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return []
        if not _persona_value(self, 'enable_recall_message_cache', True):
            return []
        self._cleanup_recalled_message_ids()
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            return []
        scope = _single_line(scope, 160)
        rows: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        for message_id, item in recalled.items():
            if not isinstance(item, dict):
                continue
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            if not message:
                continue
            item_scope = _single_line(item.get("scope") or message.get("scope"), 160)
            if scope and item_scope and item_scope != scope:
                continue
            unique_id = _single_line(message.get("message_id"), 120) or message_id
            if unique_id in seen_messages:
                continue
            seen_messages.add(unique_id)
            rows.append({**message, "message_id": message_id, "recalled_ts": _safe_float(item.get("ts"), 0)})
        rows.sort(key=lambda row: _safe_float(row.get("recalled_ts"), 0), reverse=True)
        return rows[: max(1, limit)]

    def _format_recalled_messages_for_event(self, event: AstrMessageEvent, *, limit: int = 5) -> str:
        rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
        if not rows:
            return "当前会话没有可转述的撤回消息，或缓存已经过期。"
        lines = ["最近撤回消息："]
        for index, row in enumerate(rows, 1):
            sender = _single_line(row.get("sender_name"), 40) or _single_line(row.get("sender_id"), 40) or "未知"
            text = _single_line(row.get("text"), 360)
            if row.get("cache_miss"):
                text = "[已收到撤回通知，但原消息没有进入短期缓存，无法恢复内容]"
            image_status = self._recall_image_status_summary(row)
            if image_status:
                text = f"{text}（{image_status}）"
            elapsed = self._format_timestamp_elapsed(row.get("recalled_ts", 0))
            lines.append(f"{index}. {sender}｜{elapsed}撤回：{text}")
        return "\n".join(lines)

    def _image_component_for_recall_source(self, source: str) -> Any | None:
        text = str(source or "").strip()
        if not text:
            return None
        local_text = text[len("file://"):] if text.startswith("file://") else text
        if not re.match(r"^https?://", text, flags=re.I) and not text.startswith(("data:", "base64://")):
            try:
                path = Path(local_text)
                exists = path.exists() and path.is_file()
            except (OSError, ValueError):
                exists = False
            if exists:
                text = str(path)
                for method_name in ("fromFileSystem", "from_file_system"):
                    method = getattr(Image, method_name, None)
                    if callable(method):
                        try:
                            return method(text)
                        except Exception:
                            continue
            elif re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\|file://)", text):
                return None
        candidates = (
            {"file": text, "url": text},
            {"file": text},
            {"url": text},
        )
        for kwargs in candidates:
            try:
                return Image(**kwargs)
            except Exception:
                continue
        return None

    def _recalled_message_media_components_for_event(self, event: AstrMessageEvent, *, limit: int = 5) -> list[Any]:
        rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
        components: list[Any] = []
        for index, row in enumerate(rows, 1):
            images = [
                item.get("source", "")
                for item in self._recall_image_items_from_snapshot(row)
                if item.get("source")
            ]
            image_components: list[Any] = []
            for source in images[:5]:
                component = self._image_component_for_recall_source(str(source or ""))
                if component is not None:
                    image_components.append(component)
            if not image_components:
                continue
            components.append(Plain(f"\n第 {index} 条撤回原图："))
            components.extend(image_components)
        return components

    def _user_asks_recalled_messages(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        if not any(token in compact for token in ("撤回", "撤了", "撤掉", "收回", "防撤回")):
            return False
        ask_tokens = (
            "什么", "啥", "哪条", "哪句", "内容", "刚才", "刚刚", "最近", "上一条",
            "谁", "看", "看看", "说了什么", "发了什么", "发的啥", "撤的啥",
        )
        if any(token in compact for token in ask_tokens):
            return True
        return bool(re.search(r"撤[回了掉]?.*(说|发|讲|聊)", compact))

    def _format_recalled_messages_for_natural_query_prompt_section(
        self,
        event: AstrMessageEvent,
        *,
        limit: int = 5,
    ) -> PromptSection:
        if not _persona_value(self, 'enable_recall_enhancement', True) or not _persona_value(self, 'enable_recall_transcribe_command', True):
            body = (
                "用户正在问当前会话刚才撤回了什么,但撤回消息转述功能没有开启。请自然说明这边看不到可转述的撤回内容。"
            )
        else:
            try:
                is_private = bool(getattr(event, "is_private_chat", lambda: False)())
            except Exception:
                is_private = False
            allowed = self._can_manage_private_companion(event) if is_private else self._can_manage_group_companion(event)
            if not allowed:
                body = (
                    "用户正在问当前会话刚才撤回了什么,但这类内容只能由 Bot 管理员、配置目标用户或群管理员查看。"
                    "请自然说明权限边界,不要猜测或编造撤回内容。"
                )
            else:
                rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
                if not rows:
                    body = (
                        "用户正在问当前会话刚才撤回了什么,但当前会话没有可转述的撤回消息,或短期缓存已经过期。"
                        "请自然说明没有查到,不要编造。"
                    )
                else:
                    lines = [
                        "用户正在问当前会话刚才撤回了什么。下面是可转述的短期撤回记录；请用自然口吻回答,不要提插件、缓存或内部记录机制。",
                    ]
                    for index, row in enumerate(rows, 1):
                        sender = _single_line(row.get("sender_name"), 40) or _single_line(row.get("sender_id"), 40) or "未知"
                        text = _single_line(row.get("text"), 360)
                        if row.get("cache_miss"):
                            text = "[已收到撤回通知，但原消息没有进入短期缓存，不能编造具体内容]"
                        image_status = self._recall_image_status_summary(row)
                        if image_status:
                            text = f"{text}（{image_status}；如需可恢复图片,请使用撤回消息命令查看）"
                        elapsed = self._format_timestamp_elapsed(row.get("recalled_ts", 0))
                        lines.append(f"{index}. {sender}｜{elapsed}撤回：{text}")
                    body = "\n".join(lines)
        return prompt_section(
            key="recall.query",
            title="撤回消息查询",
            source="event_dispatch",
            content=body,
        )

    def _format_recalled_messages_for_natural_query(
        self,
        event: AstrMessageEvent,
        *,
        limit: int = 5,
    ) -> str:
        section = self._format_recalled_messages_for_natural_query_prompt_section(
            event,
            limit=limit,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _forbidden_recall_words(self) -> list[str]:
        words = _persona_value(self, 'recall_forbidden_words', [])
        return [str(item) for item in words if str(item or "").strip()]

    def _forbidden_recall_hit(self, text: str) -> str:
        if not _persona_value(self, 'enable_recall_enhancement', True):
            return ""
        if not _persona_value(self, 'enable_forbidden_word_recall', False):
            return ""
        text = str(text or "")
        if not text:
            return ""
        case_sensitive = bool(_persona_value(self, 'recall_forbidden_word_case_sensitive', False))
        haystack = text if case_sensitive else text.lower()
        for word in self._forbidden_recall_words():
            needle = word if case_sensitive else word.lower()
            if needle and needle in haystack:
                return word
        return ""

    def _chain_text_for_forbidden_recall(self, chain: list[Any], *, limit: int = 2000) -> str:
        parts = [self._recall_component_text(comp) for comp in chain or []]
        return _single_line(" ".join(item for item in parts if item), limit)
