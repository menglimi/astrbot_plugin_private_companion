# -*- coding: utf-8 -*-
"""参考图命令域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 878 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import uuid
from .command_handlers_shared import _PHOTO_REFERENCE_SUFFIXES, logger
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _set_into_config, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import (
    CATALOG_VERSION,
    CatalogValidationError,
    PhotoReference,
    add_reference,
    delete_reference,
    load_catalog,
    validate_and_serialize,
)
from astrbot.api.event import AstrMessageEvent
from dataclasses import replace
from pathlib import Path
from typing import Any



class CommandHandlersPhotoReferenceMixin:
    """参考图命令域（从 CommandHandlersMixin 拆出）。"""


    def _daily_outfit_command_payload(self) -> tuple[str, str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        today = _today_key()
        if not item:
            if not bool(runtime_persona_setting(self, 'enable_daily_outfit_photo', False)):
                return (
                    "今天还没有每日穿搭图；每日穿搭照片当前没有开启。\n"
                    "需要的话，管理员可以在配置页开启“每日穿搭照片”，或手动用：陪伴 生成穿搭。",
                    "",
                )
            return "今天还没有生成每日穿搭图。管理员可以手动用：陪伴 生成穿搭。", ""
        date_key = _single_line(item.get("date"), 20)
        error = _single_line(item.get("error"), 180)
        note = _single_line(item.get("note"), 160)
        if date_key and date_key != today:
            suffix = f"\n上一次记录是 {date_key}。"
            if error:
                suffix += f"\n上次失败原因：{error}"
            return "今天还没有新的每日穿搭图。" + suffix + "\n管理员可以手动用：陪伴 生成穿搭。", ""
        path_text = _path_text(item.get("path"), 1000)
        if not path_text:
            reason = error or note or "没有可用图片路径"
            retry_count = int(item.get("retry_count", 0) or 0)
            retry_max = 5
            if retry_count > 0 and retry_count < retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n正在自动重试（第{retry_count}/{retry_max}次），稍后再来看看，或管理员手动用：陪伴 生成穿搭。", ""
            elif retry_count >= retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n已重试{retry_max}次仍未成功，管理员可以手动用：陪伴 生成穿搭。", ""
            return f"今天的每日穿搭图还没生成成功：{reason}\n管理员可以手动用：陪伴 生成穿搭。", ""
        try:
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / path
            path = path.resolve()
        except Exception:
            path = Path(path_text)
        try:
            exists = path.exists() and path.is_file()
        except (OSError, ValueError):
            exists = False
        if not exists:
            return "今天的每日穿搭图记录存在，但图片文件已经找不到了。\n管理员可以手动用：陪伴 生成穿搭。", ""
        if path.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
            return "今天的每日穿搭图记录存在，但图片格式不支持发送。管理员可以重新生成一次。", ""
        meta_parts = []
        generated_at = _safe_float(item.get("generated_at"), 0.0, 0.0)
        formatter = getattr(self, "_format_timestamp_elapsed", None)
        if generated_at > 0 and callable(formatter):
            meta_parts.append(f"生成：{formatter(generated_at)}")
        backend = _single_line(item.get("backend"), 40)
        if backend:
            meta_parts.append(f"后端：{backend}")
        caption = "今天的穿搭图在这里。"
        if meta_parts:
            caption += "\n" + "｜".join(meta_parts)
        return caption, str(path)

    def _photo_reference_image_dir(self) -> Path:
        target_dir = Path(self.data_dir) / "photo_reference_images"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _photo_reference_stem(self, stem: str = "reference") -> str:
        clean = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(stem or "reference")).strip("._")
        if not clean:
            clean = "reference"
        return f"{clean}_{int(_now_ts() * 1000)}_{uuid.uuid4().hex[:8]}"

    def _photo_reference_path_within_data_dir(self, path: Path) -> bool:
        """Return whether a reference path is inside this plugin's data tree."""
        try:
            root = Path(self.data_dir).resolve()
            resolved = path.resolve()
        except Exception:
            return False
        try:
            return resolved.is_relative_to(root)
        except AttributeError:  # Python < 3.9
            return str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep)

    def _photo_reference_copy_local_file(
        self,
        source_path: Path,
        *,
        stem: str = "reference",
        trusted: bool = True,
    ) -> str:
        """Copy a reference image into the plugin-owned reference directory.

        Untrusted/model-controlled sources may only read files below the plugin
        data directory. Explicit administrator configuration keeps the legacy
        trusted behavior for paths outside that directory.
        """
        try:
            resolved = source_path.resolve()
        except Exception:
            resolved = source_path
        if not trusted and not self._photo_reference_path_within_data_dir(resolved):
            logger.warning(
                "参考图越权本地路径已拒绝: %s",
                _single_line(str(resolved), 200),
            )
            return ""
        if not resolved.exists() or not resolved.is_file():
            return ""
        suffix = resolved.suffix.lower()
        if suffix not in _PHOTO_REFERENCE_SUFFIXES:
            return ""
        target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
        shutil.copy2(resolved, target)
        return str(target.resolve())

    def _photo_reference_write_data_image(self, source: str, *, stem: str = "reference") -> str:
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
                suffix = ".png" if "png" in lowered else ".webp" if "webp" in lowered else ".jpg"
            else:
                return ""
            if not raw:
                return ""
            target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
            target.write_bytes(raw)
            return str(target.resolve())
        except Exception:
            return ""

    async def _photo_reference_source_to_stable_path(
        self,
        source: str,
        *,
        stem: str = "reference",
        event: AstrMessageEvent | None = None,
        trusted: bool = True,
    ) -> str:
        """Normalize a reference source into a stable plugin-local path.

        Model-controlled sources are restricted to plugin data paths and public
        remote hosts; administrator-configured sources retain the legacy trust
        boundary.
        """
        text = str(source or "").strip()
        if not text:
            return ""
        data_path = self._photo_reference_write_data_image(text, stem=stem)
        if data_path:
            return data_path
        if re.match(r"^https?://", text, flags=re.I):
            downloader = getattr(self, "_persist_private_remote_image_source", None)
            if callable(downloader):
                try:
                    downloaded = await downloader(
                        text,
                        self._photo_reference_image_dir(),
                        self._photo_reference_stem(f"{stem}_remote"),
                        public_hosts_only=not trusted,
                    )
                except TypeError:
                    # Older mixins may not accept the security keyword; reject
                    # untrusted input instead of silently downgrading it.
                    if not trusted:
                        return ""
                    try:
                        downloaded = await downloader(
                            text,
                            self._photo_reference_image_dir(),
                            self._photo_reference_stem(f"{stem}_remote"),
                        )
                    except Exception:
                        downloaded = ""
                except Exception:
                    downloaded = ""
                if downloaded:
                    copied = self._photo_reference_copy_local_file(
                        Path(downloaded),
                        stem=stem,
                        trusted=trusted,
                    )
                    if copied:
                        return copied
                    if trusted:
                        return str(downloaded)
            return ""
        local_text = text[len("file://"):] if text.startswith("file://") else text
        try:
            copied = self._photo_reference_copy_local_file(
                Path(local_text),
                stem=stem,
                trusted=trusted,
            )
            if copied:
                return copied
        except (OSError, ValueError):
            pass
        resolver = getattr(self, "_qzone_resolve_onebot_image_source", None)
        if callable(resolver) and event is not None:
            try:
                resolved = await resolver(event, text)
            except Exception:
                resolved = ""
            if resolved and resolved != text:
                return await self._photo_reference_source_to_stable_path(
                    resolved,
                    stem=stem,
                    event=event,
                    trusted=trusted,
                )
        return ""

    async def _photo_reference_sources_from_current_event(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        sources: list[str] = []

        # Keep persisted message images in the same platform/account scope as
        # the active user profile.  Callers from older command paths may still
        # pass the raw sender ID, so resolve from the event here as the single
        # boundary rather than relying on every caller to do it correctly.
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                raw_sender = event.get_sender_id()
            except Exception:
                raw_sender = ""
            if raw_sender:
                try:
                    scoped = _single_line(resolver(event, raw_sender), 160)
                except Exception:
                    scoped = ""
                if scoped:
                    user_id = scoped

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        persister = getattr(self, "_persist_private_inbound_images", None)
        if callable(persister):
            try:
                for source in await persister(event, user_id):
                    add(source)
            except Exception:
                pass
        raw_extractor = getattr(self, "_raw_private_image_sources", None)
        if callable(raw_extractor):
            try:
                for source in raw_extractor(event):
                    add(source)
            except Exception:
                pass
        return sources

    def _photo_reference_sources_from_reply_cache(self, event: AstrMessageEvent) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        cleanup = getattr(self, "_cleanup_recall_message_cache", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            return sources
        id_getter = getattr(self, "_event_reply_message_ids", None)
        message_ids = id_getter(event) if callable(id_getter) else []
        scope_getter = getattr(self, "_event_scope_key", None)
        current_scope = _single_line(scope_getter(event), 160) if callable(scope_getter) else ""
        item_getter = getattr(self, "_recall_image_items_from_snapshot", None)
        for message_id in message_ids:
            snapshot = cache.get(message_id)
            if not isinstance(snapshot, dict):
                continue
            snapshot_scope = _single_line(snapshot.get("scope"), 160)
            if current_scope and snapshot_scope and snapshot_scope != current_scope:
                continue
            if callable(item_getter):
                try:
                    items = item_getter(snapshot)
                except Exception:
                    items = []
            else:
                raw_items = snapshot.get("image_items") if isinstance(snapshot.get("image_items"), list) else []
                items = [item for item in raw_items if isinstance(item, dict)]
            for item in items:
                if not isinstance(item, dict):
                    continue
                tier = _single_line(item.get("tier"), 40)
                source = str(item.get("source") or "").strip()
                if not source or tier in {"placeholder", "platform_file"}:
                    continue
                add(source)
            for source in snapshot.get("images") if isinstance(snapshot.get("images"), list) else []:
                add(source)
        return sources

    async def _photo_reference_sources_from_reply_event(self, event: AstrMessageEvent) -> list[str]:
        cached = getattr(event, "_private_companion_photo_reply_sources", None)
        if isinstance(cached, list):
            return [str(item).strip() for item in cached if str(item or "").strip()]
        sources: list[str] = []
        finder = getattr(self, "_find_reply_image_sources_for_event", None)
        if callable(finder):
            try:
                for source in await finder(event):
                    text = str(source or "").strip()
                    if text and text not in sources:
                        sources.append(text)
            except Exception:
                sources = []
        try:
            setattr(event, "_private_companion_photo_reply_sources", list(sources))
        except Exception:
            pass
        return sources

    async def _photo_reference_event_bound_stable_path(
        self,
        event: AstrMessageEvent,
        user_id: str,
        source: str,
        *,
        stem: str = "event_reference",
    ) -> str:
        """Persist a model-supplied source only when the active event owns it."""
        requested = str(source or "").strip()
        if not requested:
            return ""

        cache_name = "_private_companion_event_bound_reference_sources"
        cached = getattr(event, cache_name, None)
        if isinstance(cached, tuple):
            candidates = list(cached)
        else:
            candidates: list[str] = []

            def add(values: Any) -> None:
                for value in values if isinstance(values, (list, tuple, set)) else ():
                    text = str(value or "").strip()
                    if text and text not in candidates:
                        candidates.append(text)

            add(await self._photo_reference_sources_from_current_event(event, user_id))
            add(self._photo_reference_sources_from_reply_cache(event))
            add(await self._photo_reference_sources_from_reply_event(event))
            try:
                setattr(event, cache_name, tuple(candidates))
            except Exception:
                pass

        def local_identity(value: str) -> str:
            text = str(value or "").strip()
            if not text or re.match(r"^https?://", text, flags=re.I):
                return ""
            if text.startswith("file://"):
                text = text[len("file://"):]
            try:
                return os.path.normcase(str(Path(text).expanduser().resolve()))
            except (OSError, ValueError):
                return ""

        requested_local = local_identity(requested)
        matched = next(
            (
                candidate
                for candidate in candidates
                if candidate == requested
                or (
                    requested_local
                    and local_identity(candidate) == requested_local
                )
            ),
            "",
        )
        if not matched:
            return ""
        return await self._photo_reference_source_to_stable_path(
            matched,
            stem=stem,
            event=event,
            trusted=True,
        )

    async def _photo_reference_image_from_command_context(
        self,
        event: AstrMessageEvent,
        user_id: str,
    ) -> tuple[str, str, bool]:
        images, saw_image = await self._photo_reference_images_from_command_context(event, user_id, limit=1)
        if images:
            return images[0][0], images[0][1], True
        return "", "", saw_image

    async def _photo_reference_images_from_command_context(
        self,
        event: AstrMessageEvent,
        user_id: str,
        *,
        limit: int = 12,
    ) -> tuple[list[tuple[str, str]], bool]:
        images: list[tuple[str, str]] = []
        saw_image = False
        seen_sources: set[str] = set()
        seen_fingerprints: set[str] = set()

        def file_fingerprint(path_text: str) -> str:
            try:
                path = Path(path_text).resolve()
                if not path.is_file():
                    return ""
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                return f"{path.stat().st_size}:{digest.hexdigest()}"
            except (OSError, ValueError):
                return ""

        def remove_duplicate_copy(path_text: str) -> None:
            try:
                path = Path(path_text).resolve()
                base = self._photo_reference_image_dir().resolve()
                if path.is_file() and path.is_relative_to(base):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass

        async def collect(sources: list[str], label: str, stem: str) -> None:
            nonlocal saw_image
            for source in sources:
                saw_image = True
                source_key = str(source or "").strip()
                if not source_key or source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                if len(images) >= max(1, limit):
                    return
                path = await self._photo_reference_source_to_stable_path(source, stem=stem, event=event)
                if not path or any(existing_path == path for existing_path, _ in images):
                    continue
                fingerprint = file_fingerprint(path)
                if fingerprint and fingerprint in seen_fingerprints:
                    remove_duplicate_copy(path)
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                images.append((path, label))

        await collect(await self._photo_reference_sources_from_current_event(event, user_id), "随消息发送的图片", "message_library")
        if len(images) < max(1, limit):
            await collect(self._photo_reference_sources_from_reply_cache(event), "引用消息里的图片", "reply_library")
        if len(images) < max(1, limit):
            await collect(await self._photo_reference_sources_from_reply_event(event), "引用消息里的图片", "reply_library")
        return images, saw_image

    def _resolve_photo_reference_command_path(self, value: str) -> tuple[str, str]:
        raw = _path_text(value, 1000)
        if not raw:
            return "", "请这样设置：陪伴 参考图 <本地图片路径或图片URL>"
        if re.match(r"^https?://", raw, flags=re.I):
            return raw, ""
        expanded = os.path.expandvars(os.path.expanduser(raw))
        candidates = [Path(expanded)]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / expanded)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if not resolved.exists() or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
                return "", "参考图只支持 png、jpg、jpeg、webp。"
            return str(resolved), ""
        return "", "没有找到这张本地图片。请确认路径存在，并且 Bot 所在机器能访问；也可以直接填写 http(s) 图片 URL。"

    async def _set_photo_reference_config_path(self, path: str) -> bool:
        clean = _path_text(path, 1000)
        if runtime_persona_setting(self, 'photo_reference_catalog', None) is None:
            previous = _path_text(runtime_persona_setting(self, 'photo_persona_reference_image_path', ""), 1000)
            self.photo_persona_reference_image_path = clean
            try:
                saved = _set_into_config(self.config, "photo_persona_reference_image_path", clean)
                if saved and not await self._save_config_if_possible():
                    self.photo_persona_reference_image_path = previous
                    _set_into_config(self.config, "photo_persona_reference_image_path", previous)
                    return False
                return bool(saved)
            except Exception:
                self.photo_persona_reference_image_path = previous
                try:
                    _set_into_config(self.config, "photo_persona_reference_image_path", previous)
                except Exception:
                    pass
                return False
        try:
            catalog = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            persona = next(
                (item for item in catalog if isinstance(item, PhotoReference) and item.kind == "persona"),
                None,
            )
            if clean and persona is not None:
                updated = tuple(replace(item, source=clean) if item.id == persona.id else item for item in catalog)
            elif clean:
                updated = add_reference(
                    catalog,
                    kind="persona",
                    source=clean,
                    note="基础人物身份和外貌参考；没有更匹配的服装场景参考图时使用",
                    preset_names=self._photo_generation_scene_presets().keys(),
                )
            elif persona is not None:
                updated = delete_reference(catalog, persona.id)
            else:
                updated = catalog
            return await self._set_photo_reference_catalog_config(updated)
        except (CatalogValidationError, KeyError, TypeError, ValueError) as exc:
            logger.warning("保存 persona 参考图失败: %s", _single_line(exc, 160))
            return False

    async def _set_photo_reference_catalog_config(self, items: Any) -> bool:
        if bool(getattr(self, "photo_reference_catalog_read_only", False)):
            logger.warning("参考图目录当前为只读状态，拒绝覆盖原配置；请在管理页校验并保存目录")
            return False
        previous = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
        previous_version = _safe_int(runtime_persona_setting(self, 'photo_reference_catalog_version', 0), 0, 0)
        previous_user_cleared = bool(runtime_persona_setting(self, 'photo_reference_catalog_user_cleared', False))
        preset_names = self._photo_generation_scene_presets().keys()
        try:
            serialized = validate_and_serialize(items, preset_names=preset_names)
            loaded = load_catalog(serialized, catalog_version=CATALOG_VERSION, preset_names=preset_names)
            previous_serialized = validate_and_serialize(previous, preset_names=preset_names)
            self.photo_reference_catalog = loaded.references
            self.photo_reference_catalog_version = CATALOG_VERSION
            self.photo_reference_catalog_user_cleared = not bool(loaded.references)
            catalog_set = _set_into_config(self.config, "photo_reference_catalog", serialized)
            version_set = _set_into_config(self.config, "photo_reference_catalog_version", CATALOG_VERSION)
            cleared_set = _set_into_config(
                self.config,
                "photo_reference_catalog_user_cleared",
                runtime_persona_setting(self, 'photo_reference_catalog_user_cleared', False),
            )
            if not catalog_set or not version_set or not cleared_set or not await self._save_config_if_possible():
                self.photo_reference_catalog = previous
                self.photo_reference_catalog_version = previous_version
                self.photo_reference_catalog_user_cleared = previous_user_cleared
                _set_into_config(self.config, "photo_reference_catalog", previous_serialized)
                _set_into_config(self.config, "photo_reference_catalog_version", previous_version)
                _set_into_config(self.config, "photo_reference_catalog_user_cleared", previous_user_cleared)
                return False
            return True
        except Exception as exc:
            self.photo_reference_catalog = previous
            self.photo_reference_catalog_version = previous_version
            self.photo_reference_catalog_user_cleared = previous_user_cleared
            try:
                _set_into_config(
                    self.config,
                    "photo_reference_catalog",
                    validate_and_serialize(previous, preset_names=preset_names),
                )
                _set_into_config(self.config, "photo_reference_catalog_version", previous_version)
                _set_into_config(self.config, "photo_reference_catalog_user_cleared", previous_user_cleared)
            except Exception:
                pass
            logger.warning("保存规范参考图目录失败: %s", _single_line(exc, 180))
            return False

    async def _set_photo_reference_library_config(self, items: list[Any]) -> bool:
        if runtime_persona_setting(self, 'photo_reference_catalog', None) is None:
            normalized: list[Any] = []
            seen_sources: set[str] = set()
            for raw_item in items[:24]:
                if isinstance(raw_item, dict):
                    source = _path_text(raw_item.get("source") or raw_item.get("path") or raw_item.get("url"), 1000)
                    if not source or source in seen_sources:
                        continue
                    seen_sources.add(source)
                    normalized.append(dict(raw_item))
                    continue
                text = str(raw_item or "").strip()
                source = re.split(r"\s*(?:\|\||｜｜)\s*", text, maxsplit=1)[0].strip() if text else ""
                if text and source not in seen_sources:
                    seen_sources.add(source)
                    normalized.append(text[:3000])
            previous = list(runtime_persona_setting(self, 'photo_reference_library', []) or [])
            self.photo_reference_library = normalized
            try:
                saved = _set_into_config(self.config, "photo_reference_library", normalized)
                if saved and not await self._save_config_if_possible():
                    self.photo_reference_library = previous
                    _set_into_config(self.config, "photo_reference_library", previous)
                    return False
                return bool(saved)
            except Exception:
                self.photo_reference_library = previous
                try:
                    _set_into_config(self.config, "photo_reference_library", previous)
                except Exception:
                    pass
                return False
        try:
            preset_names = self._photo_generation_scene_presets().keys()
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=items,
                preset_names=preset_names,
            )
            catalog = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            kept = tuple(
                item
                for item in catalog
                if isinstance(item, PhotoReference) and item.kind != "library"
            )
            return await self._set_photo_reference_catalog_config((*kept, *loaded.references))
        except (CatalogValidationError, TypeError, ValueError) as exc:
            logger.warning("保存兼容参考图库失败: %s", _single_line(exc, 160))
            return False

    async def _photo_reference_library_command_payload(
        self,
        event: AstrMessageEvent,
        user_id: str,
        value: str = "",
    ) -> tuple[str, str]:
        action = str(value or "").strip()
        entries_getter = getattr(self, "_photo_reference_library_entries", None)
        entries = entries_getter() if callable(entries_getter) else []
        if action in {"", "列表", "查看", "状态", "list", "show"}:
            if not entries:
                return (
                    "参考图库目前为空。\n"
                    "发送一张或多张图片并附上：陪伴 参考图库 添加 居家服，在家、卧室、睡前使用\n"
                    "也可以在陪伴面板按“路径或 URL || 用途注释”一行一张填写。"
                ), ""
            lines = [f"参考图库：{len(entries)}/24 张"]
            for index, item in enumerate(entries, start=1):
                summary = [
                    f"职责={','.join(item.get('reference_roles') or []) or '-'}",
                    f"服装={_single_line(item.get('outfit_category'), 50) or '-'}",
                    f"锁定={'是' if item.get('outfit_lock_default') else '否'}",
                    f"场景={','.join(item.get('scene_categories') or []) or '-'}",
                    f"时间={','.join(item.get('time_categories') or []) or '-'}",
                    f"预设={_single_line(item.get('preferred_preset'), 60) or '-'}",
                ]
                lines.append(
                    f"{index}. {_single_line(item.get('note'), 160)}\n"
                    f"   ID={_single_line(item.get('id'), 80)}｜{'｜'.join(summary)}\n"
                    f"   {_single_line(item.get('source'), 260)}"
                )
            lines.append("预览：陪伴 参考图库 预览 编号；删除：陪伴 参考图库 删除 编号或ID")
            return "\n".join(lines), ""
        preview_match = re.match(r"^(?:预览|查看|preview|show)\s*(\d{1,2})$", action, flags=re.I)
        if preview_match:
            index = int(preview_match.group(1)) - 1
            if not 0 <= index < len(entries):
                return "没有这个编号的参考图。", ""
            item = entries[index]
            path = self._photo_reference_local_path(item.get("source", "")) if callable(getattr(self, "_photo_reference_local_path", None)) else ""
            if not path and re.match(r"^https?://", item.get("source", ""), flags=re.I):
                path = await self._photo_reference_source_to_stable_path(item["source"], stem=f"library_preview_{index + 1}")
            if not path:
                return f"第 {index + 1} 张参考图当前不可用，请检查路径或 URL。", ""
            summary = [
                f"职责={','.join(item.get('reference_roles') or []) or '-'}",
                f"服装={_single_line(item.get('outfit_category'), 50) or '-'}",
                f"锁定={'是' if item.get('outfit_lock_default') else '否'}",
                f"场景={','.join(item.get('scene_categories') or []) or '-'}",
                f"时间={','.join(item.get('time_categories') or []) or '-'}",
                f"预设={_single_line(item.get('preferred_preset'), 60) or '-'}",
            ]
            return (
                f"参考图 {index + 1}：{_single_line(item.get('note'), 260)}\n"
                f"ID={_single_line(item.get('id'), 80)}｜{'｜'.join(summary)}",
                path,
            )
        role_match = re.match(
            r"^(?:设置|设为|职责)\s+([0-9A-Za-z_-]{1,80})\s+(仅身份|仅服装|仅姿势|仅场景|仅画风|身份|服装|姿势|场景|画风)$",
            action,
            flags=re.I,
        )
        if role_match:
            identifier, shortcut = role_match.groups()
            if identifier.isdigit():
                index = int(identifier) - 1
                if not 0 <= index < len(entries):
                    return "没有这个编号的参考图。", ""
                selected = entries[index]
            else:
                selected = next((item for item in entries if item.get("id") == identifier), None)
                if selected is None:
                    return "没有这个 ID 的参考图。", ""
                index = entries.index(selected)
            role = {
                "仅身份": "identity",
                "身份": "identity",
                "仅服装": "outfit",
                "服装": "outfit",
                "仅姿势": "pose",
                "姿势": "pose",
                "仅场景": "scene",
                "场景": "scene",
                "仅画风": "style",
                "画风": "style",
            }[shortcut]
            updated: list[PhotoReference] = []
            for reference in tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()):
                if not isinstance(reference, PhotoReference):
                    continue
                if reference.id == selected.get("id"):
                    reference = replace(
                        reference,
                        reference_roles=(role,),
                        outfit_lock_default=role == "outfit",
                        metadata_source="configured",
                    )
                updated.append(reference)
            saved = await self._set_photo_reference_catalog_config(tuple(updated))
            return (
                f"已将参考图 {index + 1} 设置为仅承担 {role} 职责。"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), ""
        delete_match = re.match(r"^(?:删除|移除|delete|remove)\s+([0-9A-Za-z_-]{1,80})$", action, flags=re.I)
        if delete_match:
            identifier = delete_match.group(1)
            if identifier.isdigit():
                index = int(identifier) - 1
                if not 0 <= index < len(entries):
                    return "没有这个编号的参考图。", ""
                removed = entries[index]
            else:
                removed = next((item for item in entries if item.get("id") == identifier), None)
                if removed is None:
                    return "没有这个 ID 的参考图。", ""
                index = entries.index(removed)
            try:
                kept = delete_reference(
                    tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()),
                    removed["id"],
                )
            except KeyError:
                return "这张参考图已经不存在。", ""
            saved = await self._set_photo_reference_catalog_config(kept)
            return (
                f"已从参考图库删除第 {index + 1} 张：{_single_line(removed.get('note'), 160)}"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), ""
        if action in {"清空", "全部清空", "clear", "clear all"}:
            kept = tuple(
                item
                for item in (runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
                if isinstance(item, PhotoReference) and item.kind != "library"
            )
            saved = await self._set_photo_reference_catalog_config(kept)
            return "已清空参考图库。" + ("" if saved else "\n但配置保存可能失败，请到面板确认。"), ""

        add_match = re.match(r"^(?:添加|上传|新增|add|upload)(?:\s+([\s\S]*))?$", action, flags=re.I)
        if add_match:
            note = _single_line(add_match.group(1), 500) or "通用人物参考图；没有更具体的服装或场景匹配时使用"
            images, saw_image = await self._photo_reference_images_from_command_context(event, user_id, limit=12)
            if not images:
                if saw_image:
                    return "找到了图片，但没能保存为参考图；请确认是 png、jpg、jpeg 或 webp。", ""
                return "请把一张或多张图片与命令一起发送，或回复图片后发送“陪伴 参考图库 添加 用途注释”。", ""
            current = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            available = max(0, 24 - len(entries))
            added = images[:available]
            if not added:
                return "参考图库已达到 24 张上限，请先删除不用的图片。", ""
            try:
                updated = current
                for path, _label in added:
                    updated = add_reference(
                        updated,
                        kind="library",
                        source=path,
                        note=note,
                        preset_names=self._photo_generation_scene_presets().keys(),
                    )
            except CatalogValidationError as exc:
                return f"参考图元数据校验失败：{_single_line(exc, 300)}", ""
            saved = await self._set_photo_reference_catalog_config(updated)
            return (
                f"已向参考图库添加 {len(added)} 张图片。\n用途注释：{note}\n"
                "生成时会结合地点、服装和画面要求自动选择其中一张；今日穿搭图不再无条件优先。"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), added[0][0]
        return (
            "可用命令：\n"
            "陪伴 参考图库 添加 <用途注释>（可同时携带多张图）\n"
            "陪伴 参考图库 列表\n"
            "陪伴 参考图库 预览 <编号>\n"
            "陪伴 参考图库 设置 <编号> <仅身份|仅服装|仅姿势|仅场景|仅画风>\n"
            "陪伴 参考图库 删除 <编号>\n"
            "陪伴 参考图库 清空"
        ), ""

    async def _photo_reference_command_text(self, event: AstrMessageEvent, user_id: str, value: str = "") -> str:
        text, _ = await self._photo_reference_command_payload(event, user_id, value)
        return text

    async def _photo_reference_command_payload(self, event: AstrMessageEvent, user_id: str, value: str = "") -> tuple[str, str]:
        action = _single_line(value, 1000)
        if action in {"清空", "删除", "移除", "clear", "none", "空"}:
            saved = await self._set_photo_reference_config_path("")
            return "已清空主动自拍人设参考图。" + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。"), ""
        force_image = action in {"图片", "这张", "这张图", "引用", "引用图", "引用图片", "设置", "更换", "更新", "添加", "上传", "用这张", "使用这张"}
        preview_actions = {"查看", "状态", "当前", "预览", "检查", "发出来", "发图", "看看", "current", "show", "preview"}
        if action in preview_actions:
            force_image = False
        if not action or force_image:
            image_path, image_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            if image_path:
                saved = await self._set_photo_reference_config_path(image_path)
                enabled_note = (
                    "参考图一致性已开启，会在 selfie/人像/头像/角色表情包自动生图里使用。"
                    if runtime_persona_setting(self, 'enable_photo_reference_image', False)
                    else "参考图路径已保存，但“参考图一致性”当前关闭；需要自动用于自拍/头像/角色表情包时，请在生图/拍照能力详情里开启。"
                )
                return (
                    f"已把{image_label}设为主动自拍人设参考图：\n"
                    f"{image_path}\n"
                    f"{enabled_note}\n"
                    "ComfyUI 需要支持 images=1 的自拍工作流。"
                    + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
                ), image_path
            if force_image:
                if saw_image:
                    return "找到了图片，但没能保存成参考图。参考图只支持 png、jpg、jpeg、webp；也可能是平台只给了图片 file id，拿不到原图。", ""
                return "没有在这条消息或引用消息里找到图片。可以发送图片并附上“陪伴 参考图”，或回复一条近期图片消息发送“陪伴 参考图”。", ""
        if not action or action in preview_actions:
            persona = next(
                (
                    item
                    for item in (runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            configured = _path_text(persona.source if persona is not None else "", 1000)
            resolved = self._photo_persona_reference_image_path() if callable(getattr(self, "_photo_persona_reference_image_path", None)) else ""
            enabled = bool(runtime_persona_setting(self, 'enable_photo_reference_image', False))
            if not configured:
                return (
                    f"参考图一致性：{'开启' if enabled else '关闭'}\n"
                    "当前没有设置主动自拍人设参考图。\n"
                    "设置方式：陪伴 参考图 <本地图片路径或图片URL>；也可以发送图片并附上“陪伴 参考图”。"
                ), ""
            if not resolved and re.match(r"^https?://", configured.strip(), flags=re.I):
                async_resolver = getattr(self, "_photo_persona_reference_image_path_async", None)
                if enabled and callable(async_resolver):
                    try:
                        resolved = _path_text(await async_resolver(), 1000)
                    except Exception as exc:
                        logger.info("参考图查看时 URL 下载失败: %s", _single_line(exc, 120))
                        resolved = ""
            status = "可用" if resolved else "URL 待首次使用时下载" if re.match(r"^https?://", configured.strip(), flags=re.I) else "路径不可用或格式不支持"
            return (
                f"参考图一致性：{'开启' if enabled else '关闭'}\n"
                "当前主动自拍人设参考图：\n"
                f"{configured}\n"
                f"状态：{status}"
                + ("" if enabled else "\n提示：开关关闭时不会自动用于自拍/头像/角色表情包。")
                + (f"\n实际使用文件：{resolved}" if resolved and resolved != configured else "")
            ), resolved
        path, error = self._resolve_photo_reference_command_path(action)
        if error:
            return error, ""
        stable_path = await self._photo_reference_source_to_stable_path(path, stem="manual") or path
        saved = await self._set_photo_reference_config_path(stable_path)
        enabled_note = (
            "参考图一致性已开启，会在 selfie/人像/头像/角色表情包自动生图里使用。"
            if runtime_persona_setting(self, 'enable_photo_reference_image', False)
            else "参考图路径已保存，但“参考图一致性”当前关闭；需要自动用于自拍/头像/角色表情包时，请在生图/拍照能力详情里开启。"
        )
        return (
            "已设置主动自拍人设参考图：\n"
            f"{stable_path}\n"
            f"{enabled_note}\n"
            "ComfyUI 需要支持 images=1 的自拍工作流。"
            + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
        ), stable_path
