# -*- coding: utf-8 -*-
"""WardrobeMixin — 角色衣柜的运行时接线。

数据层在 :mod:`wardrobe`，本模块只负责把它接进插件运行时：

* 读数：``runtime_persona_setting``（人格作用域）＋ 运行时属性回退。
* 落盘：``_set_into_config`` + ``_save_config_if_possible``，失败即回滚。
* 识图：复用陪伴已有的视觉 provider 解析，但用衣柜专用的衣物描述提示词。
* 提示词：产出一个 ``PromptSection``，由被动回复链注入。
* 命令：``陪伴 衣柜 ...`` 的全部子命令。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _flat_get, _set_into_config, _single_line, _today_key
from .persona_config import PERSONA_SETTINGS_KEY, runtime_persona_setting
from .wardrobe import (
    OUTFIT_KIND_BUNDLE,
    OUTFIT_KIND_STYLE,
    SOURCE_KIND_IMAGE,
    SOURCE_KIND_MANUAL,
    WARDROBE_MAX_ITEMS,
    WARDROBE_MAX_OUTFITS,
    WARDROBE_PROMPT_MAX_CHARS,
    WARDROBE_PROMPT_MAX_ITEMS,
    WardrobeError,
    WardrobeLimitError,
    add_wardrobe_item,
    add_wardrobe_outfit,
    build_wardrobe_image_instruction,
    clear_wardrobe,
    delete_wardrobe_item,
    delete_wardrobe_outfit,
    find_wardrobe_item,
    find_wardrobe_outfit,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_wardrobe_tendency,
    parse_wardrobe_image_reply,
    render_wardrobe_outfit_prompt,
    render_wardrobe_prompt,
    select_wardrobe_outfit,
    update_wardrobe_item,
    update_wardrobe_outfit,
    wardrobe_summary_lines,
)

WARDROBE_PROMPT_KEY = "wardrobe.character"

# 区分“没有这个值”和“值是 None”，回滚时据此决定是否写回。
_MISSING = object()

# 识图可能较慢；衣柜是显式命令触发的交互，可以等得久一点。
_WARDROBE_VISION_TIMEOUT_SECONDS = 90.0
_WARDROBE_VISION_MAX_IMAGES = 8


class WardrobeMixin:
    """角色衣柜：配置、识图入库、提示词与命令。"""

    # ------------------------------------------------------------------
    # 读数
    # ------------------------------------------------------------------

    @staticmethod
    def _wardrobe_bool(value: Any, default: bool = False) -> bool:
        """Coerce config booleans without treating ``"false"`` as truthy."""

        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().casefold()
            if lowered in {"true", "1", "yes", "on", "enable", "enabled", "是", "开启", "开"}:
                return True
            if lowered in {"false", "0", "no", "off", "disable", "disabled", "否", "关闭", "关", ""}:
                return False
        return default if value is None else bool(value)

    def _wardrobe_setting(self, key: str, default: Any = None) -> Any:
        getter = getattr(self, "persona_setting", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                pass
        setting = runtime_persona_setting(self, key, default)
        if setting is not None:
            return setting
        return getattr(self, key, default)

    def _wardrobe_enabled(self) -> bool:
        return self._wardrobe_bool(self._wardrobe_setting("enable_wardrobe", True), True)

    def _wardrobe_tendency(self) -> str:
        return normalize_wardrobe_tendency(self._wardrobe_setting("wardrobe_tendency", ""))

    def _wardrobe_prompt_mode(self) -> bool:
        return self._wardrobe_bool(self._wardrobe_setting("enable_wardrobe_prompt", True), True)

    def _wardrobe_prompt_item_limit(self) -> int:
        try:
            value = int(self._wardrobe_setting("wardrobe_prompt_max_items", WARDROBE_PROMPT_MAX_ITEMS))
        except (TypeError, ValueError):
            return WARDROBE_PROMPT_MAX_ITEMS
        return max(1, min(WARDROBE_MAX_ITEMS, value))

    def _wardrobe_image_limit(self) -> int:
        try:
            value = int(self._wardrobe_setting("wardrobe_image_max_count", 3))
        except (TypeError, ValueError):
            return 3
        return max(1, min(_WARDROBE_VISION_MAX_IMAGES, value))

    def _wardrobe_vision_provider_id(self) -> str:
        return _single_line(self._wardrobe_setting("WARDROBE_VISION_PROVIDER_ID", ""), 160)

    def _wardrobe_image_prompt(self) -> str:
        """Return the user-authored description prompt, or empty for the default."""

        return normalize_wardrobe_image_prompt(self._wardrobe_setting("wardrobe_image_prompt", ""))

    def _wardrobe_items(self) -> list[dict[str, Any]]:
        return normalize_wardrobe_items(self._wardrobe_setting("wardrobe_items", []))

    def _wardrobe_outfits(self) -> list[dict[str, Any]]:
        return normalize_wardrobe_outfits(self._wardrobe_setting("wardrobe_outfits", []))

    def _wardrobe_outfit_mode(self) -> str:
        """Return inventory (列出全部衣物) or select (只注入裁决出的那一套)."""

        text = _single_line(self._wardrobe_setting("wardrobe_outfit_mode", "inventory"), 20).casefold()
        return "select" if text == "select" else "inventory"

    def _wardrobe_outfit_rotation_days(self) -> int:
        """Cooldown window, kept in the same 1..30 range as the author's photo setting."""

        try:
            value = int(self._wardrobe_setting("wardrobe_outfit_rotation_days", 7))
        except (TypeError, ValueError):
            return 7
        return max(1, min(30, value))

    def _wardrobe_current_scene(self) -> str:
        """Reuse the author's scene decision; return "" when it is unavailable.

        An empty scene means "no filtering", so a plugin build that lacks
        _daily_outfit_scene_kind keeps behaving exactly as before.
        """

        decide = getattr(self, "_daily_outfit_scene_kind", None)
        if not callable(decide):
            return ""
        schedule = ""
        getter = getattr(self, "_daily_outfit_schedule_text", None)
        if callable(getter):
            try:
                schedule = str(getter() or "")
            except Exception:
                schedule = ""
        weather = ""
        getter = getattr(self, "_format_weather_for_prompt", None)
        if callable(getter):
            try:
                weather = str(getter() or "")
            except Exception:
                weather = ""
        try:
            return _single_line(decide(schedule, weather), 20)
        except Exception:
            return ""

    def _wardrobe_outfit_selection(self, user: Any = None) -> dict[str, Any]:
        """Resolve the outfit for this turn.

        Deterministic for a given (date, scene, wardrobe), so repeated calls
        inside one day never make the character change clothes mid-conversation.
        """

        user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        seed = f"{_today_key()}|{user_id}"
        return select_wardrobe_outfit(
            self._wardrobe_items(),
            self._wardrobe_outfits(),
            scene=self._wardrobe_current_scene(),
            seed=seed,
        )

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    async def _save_wardrobe_state(
        self,
        *,
        tendency: str | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Persist wardrobe config atomically; roll back the runtime on failure."""

        payload: dict[str, Any] = {}
        if tendency is not None:
            payload["wardrobe_tendency"] = normalize_wardrobe_tendency(tendency)
        if items is not None:
            payload["wardrobe_items"] = normalize_wardrobe_items(items)
        if not payload:
            return True
        runtime_attr = {
            "wardrobe_tendency": "wardrobe_tendency",
            "wardrobe_items": "wardrobe_items",
        }
        # Capture both layers so a failed save restores the exact previous
        # state instead of writing a placeholder back into the config.
        previous_runtime = {
            key: getattr(self, attr, _MISSING) for key, attr in runtime_attr.items()
        }
        active_scope_getter = getattr(self, "_active_persona_scope", None)
        active_persona = ""
        if callable(active_scope_getter):
            try:
                active_persona = str(active_scope_getter() or "").strip()
            except Exception:
                active_persona = ""
        primary_getter = getattr(self, "_primary_persona_id", None)
        primary_persona = ""
        if callable(primary_getter):
            try:
                primary_persona = str(primary_getter() or "").strip()
            except Exception:
                primary_persona = ""
        profile = None
        profile_settings = None
        if active_persona and active_persona != primary_persona:
            ensure_profile = getattr(self, "_ensure_persona_profile", None)
            if callable(ensure_profile):
                try:
                    candidate = ensure_profile(active_persona)
                    if isinstance(candidate, dict):
                        settings = candidate.get(PERSONA_SETTINGS_KEY)
                        if not isinstance(settings, dict):
                            settings = {}
                            candidate[PERSONA_SETTINGS_KEY] = settings
                        profile, profile_settings = candidate, settings
                except Exception:
                    profile = profile_settings = None
        previous_config = (
            {key: _flat_get(self.config, key, _MISSING) for key in payload}
            if profile_settings is None
            else {}
        )
        previous_profile_settings = deepcopy(profile_settings) if profile_settings is not None else None

        def rollback() -> None:
            for key, attr in runtime_attr.items():
                if key not in payload:
                    continue
                if previous_runtime[key] is _MISSING:
                    try:
                        delattr(self, attr)
                    except (AttributeError, TypeError):
                        pass
                else:
                    setattr(self, attr, previous_runtime[key])
                if profile_settings is not None and previous_profile_settings is not None:
                    profile_settings.clear()
                    profile_settings.update(deepcopy(previous_profile_settings))
                elif previous_config[key] is not _MISSING:
                    try:
                        _set_into_config(self.config, key, previous_config[key])
                    except Exception:
                        pass

        for key, value in payload.items():
            setattr(self, runtime_attr[key], value)
        try:
            if profile_settings is not None and profile is not None:
                profile_settings.update(payload)
                saver = getattr(self, "_save_persona_profile_async", None)
                if not callable(saver):
                    rollback()
                    return False
                await saver(active_persona, profile)
                return True
            saved = False
            for key, value in payload.items():
                saved = _set_into_config(self.config, key, value) or saved
            if saved and not await self._save_config_if_possible():
                rollback()
                return False
            return bool(saved)
        except Exception as exc:
            logger.warning("保存角色衣柜失败: %s", _single_line(exc, 160))
            rollback()
            return False

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------

    def _wardrobe_prompt_section(self, user: Any = None) -> PromptSection | None:
        """Build the wardrobe prompt section, or ``None`` when it should not inject."""

        if not self._wardrobe_enabled() or not self._wardrobe_prompt_mode():
            return None
        tendency = self._wardrobe_tendency()
        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        if not tendency and not items and not outfits:
            return None
        if self._wardrobe_outfit_mode() == "select":
            body = self._wardrobe_selected_outfit_body(user, tendency)
        else:
            body = render_wardrobe_prompt(
                tendency,
                items,
                max_items=self._wardrobe_prompt_item_limit(),
                max_chars=WARDROBE_PROMPT_MAX_CHARS,
                scene=self._wardrobe_current_scene(),
            )
        if not body:
            return None
        return prompt_section(
            key=WARDROBE_PROMPT_KEY,
            title="角色衣柜",
            source="wardrobe",
            content=body,
        )

    def _wardrobe_selected_outfit_body(self, user: Any = None, tendency: Any = "") -> str:
        """Body for the select mode: only the resolved outfit, not the inventory.

        Falls back to the full listing when nothing can be resolved (empty
        wardrobe, or every item filtered out by scene) so the section never
        silently becomes empty.
        """

        selection = self._wardrobe_outfit_selection(user)
        body = render_wardrobe_outfit_prompt(tendency, selection)
        if body:
            return body
        return render_wardrobe_prompt(
            tendency,
            self._wardrobe_items(),
            max_items=self._wardrobe_prompt_item_limit(),
            max_chars=WARDROBE_PROMPT_MAX_CHARS,
            scene=self._wardrobe_current_scene(),
        )

    async def _append_group_wardrobe_to_request(self, event: Any, req: Any) -> None:
        """Inject the wardrobe into a group request exactly once."""

        if req is None:
            return
        builder = getattr(self, "_wardrobe_prompt_section", None)
        if not callable(builder):
            return
        try:
            section = builder(None)
        except Exception as exc:
            logger.debug("群聊角色衣柜提示词构建失败: %s", _single_line(exc, 160))
            return
        if section is None or not section.content:
            return
        placer = getattr(self, "_place_conversation_prompt_section", None)
        if not callable(placer):
            return
        marker = "<!-- private_companion_group_wardrobe -->"
        try:
            placer(req, marker, section, priority=13)
        except Exception as exc:
            logger.debug("群聊角色衣柜注入失败: %s", _single_line(exc, 160))

    # ------------------------------------------------------------------
    # 识图：图片 → 衣物描述
    # ------------------------------------------------------------------

    def _wardrobe_vision_candidates(self, umo: str = "", preferred: str = "") -> list[str]:
        """Return ordered vision provider ids for wardrobe description."""

        ordered: list[str] = []
        override = _single_line(preferred, 160)
        if override:
            ordered.append(override)
        configured = self._wardrobe_vision_provider_id()
        if configured and configured not in ordered:
            ordered.append(configured)
        resolver = getattr(self, "_private_image_visual_provider_candidates", None)
        if callable(resolver):
            try:
                for item in resolver(umo) or ():
                    provider_id = _single_line(item[0] if item else "", 160)
                    if provider_id and provider_id not in ordered:
                        ordered.append(provider_id)
            except Exception as exc:
                logger.debug("衣柜识图 provider 解析失败: %s", _single_line(exc, 120))
        return ordered

    async def _wardrobe_describe_image(
        self,
        image_sources: list[str],
        *,
        note: str = "",
        umo: str = "",
        provider_id: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """Describe one garment image; return ``(parsed_fields, error_text)``."""

        sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()]
        if not sources:
            return None, "没有可用的图片。"
        prepare = getattr(self, "_prepare_private_image_sources_for_model", None)
        namespace = "wardrobe_vision"
        try:
            prepared = await prepare(sources, namespace=namespace) if callable(prepare) else sources
        except Exception as exc:
            logger.warning("衣柜识图图片预处理失败: %s", _single_line(exc, 160))
            return None, "图片预处理失败，请换一张再试。"
        prepared = [str(item).strip() for item in (prepared or []) if str(item or "").strip()]
        if not prepared:
            return None, "图片无法读取，请换一张再试。"
        cleanup = getattr(self, "_cleanup_prepared_image_sources", None)
        try:
            items_getter = getattr(self, "_private_image_model_image_items_with_meta", None)
            if not callable(items_getter):
                return None, "当前运行时不支持识图。"
            # 真实签名是 (image_items, source_image_count, has_gif_frames)，
            # 图片地址要从 image_items 的第二个字段取，不能直接当成第二项。
            image_items, _source_image_count, _has_gif = items_getter(prepared)
            image_urls = [
                str(url)
                for _key, url in (image_items or ())
                if str(url or "").strip()
            ]
            if not image_urls:
                return None, "图片无法读取，请换一张再试。"
            prompt = build_wardrobe_image_instruction(note, self._wardrobe_image_prompt())
            # 视觉 Provider 是直连调用，不走预算化的 _llm_call 路径，因此需要
            # 按同一约定手动补上插件任务附加指令，让面板里的「衣柜衣物识图」
            # 覆盖项生效；只影响本任务请求，不改动 AstrBot 主对话提示词。
            prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
            if callable(prompt_applier):
                prompt, _unused_system_prompt = prompt_applier(
                    "wardrobe_image",
                    prompt,
                    None,
                    flatten_system_prompt=True,
                )
            failure = "识图模型没有返回可用的衣物描述。"
            for provider_id_candidate in self._wardrobe_vision_candidates(umo, preferred=provider_id):
                provider = self._private_image_provider_by_id(provider_id_candidate)
                if provider is None or not self._provider_supports_image(provider):
                    continue
                runner = getattr(self, "_can_run_llm_task", None)
                if callable(runner) and not runner(provider_id_candidate, task="wardrobe_image"):
                    continue
                try:
                    request_call = provider.text_chat(prompt=prompt, image_urls=image_urls)
                    try:
                        result = await asyncio.wait_for(
                            request_call, timeout=_WARDROBE_VISION_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        failure = "识图超时，请稍后再试或换一张图。"
                        logger.warning("衣柜识图超时: provider=%s", provider_id_candidate)
                        continue
                except Exception as exc:
                    failure = "识图失败，请稍后再试。"
                    logger.warning(
                        "衣柜识图调用失败: provider=%s error=%s",
                        provider_id_candidate,
                        _single_line(exc, 160),
                    )
                    continue
                text = str(getattr(result, "completion_text", result) or "").strip()
                parsed = parse_wardrobe_image_reply(text)
                if parsed is None:
                    logger.info(
                        "衣柜识图返回不可用结果: provider=%s preview=%s",
                        provider_id_candidate,
                        _single_line(text, 160),
                    )
                    continue
                return parsed, ""
            return None, failure
        finally:
            if callable(cleanup):
                try:
                    cleanup(prepared, namespace=namespace)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    def _wardrobe_overview_text(self) -> str:
        tendency = self._wardrobe_tendency()
        items = self._wardrobe_items()
        lines = [f"角色衣柜：{len(items)}/{WARDROBE_MAX_ITEMS} 件"]
        lines.append(f"启用：{'是' if self._wardrobe_enabled() else '否'}；写入提示词：{'是' if self._wardrobe_prompt_mode() else '否'}")
        lines.append(f"整体服饰倾向：{tendency or '（未设置）'}")
        if items:
            lines.append("具体衣物：")
            lines.extend(wardrobe_summary_lines(items, description_limit=60))
        else:
            lines.append("具体衣物：（还没有）")
        lines.append("")
        lines.append("维护方式：")
        lines.extend(self._wardrobe_help_lines())
        return "\n".join(lines)

    @staticmethod
    def _wardrobe_help_lines() -> list[str]:
        return [
            "陪伴 衣柜 倾向 <整体服饰倾向描述>",
            "陪伴 衣柜 添加 <名称> | <描述>",
            "陪伴 衣柜 添加图片 <可选备注>（带图或回复图片发送）",
            "陪伴 衣柜 修改 <编号或名称> <新描述>",
            "陪伴 衣柜 删除 <编号或名称>",
            "陪伴 衣柜 清空",
        ]

    async def _wardrobe_command_payload(
        self,
        event: AstrMessageEvent,
        user_id: str,
        value: str = "",
    ) -> tuple[str, str]:
        """Handle ``陪伴 衣柜 ...``; returns ``(reply_text, reply_image_path)``."""

        raw = str(value or "").strip()
        parts = raw.split(maxsplit=1)
        action = parts[0].strip() if parts else ""
        argument = parts[1].strip() if len(parts) >= 2 else ""
        if not action or action in {"查看", "状态", "列表", "list"}:
            return self._wardrobe_overview_text(), ""
        if action in {"帮助", "help", "说明"}:
            return "角色衣柜用法：\n" + "\n".join(self._wardrobe_help_lines()), ""
        if action in {"倾向", "整体倾向", "风格", "tendency"}:
            return await self._wardrobe_set_tendency(argument)
        if action in {"添加", "add", "新增"}:
            return await self._wardrobe_add_text(argument)
        if action in {"添加图片", "识图添加", "图片添加", "addimage", "add_image"}:
            return await self._wardrobe_add_from_image(event, user_id, argument)
        if action in {"修改", "编辑", "edit", "update"}:
            return await self._wardrobe_update(argument)
        if action in {"删除", "移除", "delete", "remove", "del"}:
            return await self._wardrobe_delete(argument)
        if action in {"清空", "全部清空", "clear"}:
            return await self._wardrobe_clear()
        # 未识别子命令：把整段当作衣物"名称 | 描述"快速添加。
        if "|" in raw or "｜" in raw:
            return await self._wardrobe_add_text(raw)
        return "未知的衣柜子命令。\n" + "\n".join(self._wardrobe_help_lines()), ""

    async def _wardrobe_set_tendency(self, argument: str) -> tuple[str, str]:
        tendency = normalize_wardrobe_tendency(argument)
        if not tendency:
            current = self._wardrobe_tendency()
            return (
                f"当前整体服饰倾向：{current or '（未设置）'}\n"
                "要修改请发送：陪伴 衣柜 倾向 <描述>；发送“陪伴 衣柜 倾向 清空”可清除。",
                "",
            )
        if tendency in {"清空", "清除", "删除", "无", "none", "clear"}:
            tendency = ""
        saved = await self._save_wardrobe_state(tendency=tendency)
        if not saved:
            return "整体服饰倾向没有保存成功，请到面板确认配置是否可写。", ""
        if not tendency:
            return "已清除整体服饰倾向。", ""
        return f"已更新整体服饰倾向：\n{tendency}", ""

    @staticmethod
    def _parse_wardrobe_add_argument(argument: str) -> tuple[str, str]:
        text = str(argument or "").strip()
        if not text:
            return "", ""
        for separator in ("||", "｜｜", "|", "｜"):
            if separator in text:
                name, _, description = text.partition(separator)
                return name.strip(), description.strip()
        # 没有分隔符时，整段先当作描述；名称由数据层从描述中截取。
        return "", text

    async def _wardrobe_add_text(self, argument: str) -> tuple[str, str]:
        name, description = self._parse_wardrobe_add_argument(argument)
        if not name and not description:
            return (
                "请这样添加衣物：陪伴 衣柜 添加 米色针织开衫 | 宽松米色针织开衫，罗纹袖口\n"
                "只写一段描述也可以：陪伴 衣柜 添加 宽松米色针织开衫，罗纹袖口",
                "",
            )
        try:
            items, stored = add_wardrobe_item(
                self._wardrobe_items(),
                name=name or description,
                description=description if name else "",
                source_kind=SOURCE_KIND_MANUAL,
            )
        except WardrobeLimitError as exc:
            return str(exc), ""
        except WardrobeError as exc:
            return f"没有添加成功：{exc}", ""
        if not await self._save_wardrobe_state(items=items):
            return "衣物没有保存成功，请到面板确认配置是否可写。", ""
        return f"已把「{stored['name']}」加入衣柜（共 {len(items)} 件）。", ""

    async def _wardrobe_add_from_image(
        self,
        event: AstrMessageEvent,
        user_id: str,
        argument: str,
    ) -> tuple[str, str]:
        if not self._wardrobe_enabled():
            return "角色衣柜当前是关闭的，请先在角色设置里开启衣柜。", ""
        collector = getattr(self, "_photo_reference_images_from_command_context", None)
        if not callable(collector):
            return "当前运行时不支持从图片添加衣物。", ""
        limit = self._wardrobe_image_limit()
        images, saw_image = await collector(event, user_id, limit=limit)
        if not images:
            if saw_image:
                return "没有读到可用图片，请换一张再试。", ""
            return (
                "请把衣物图片和命令一起发送，或回复一张图片后发送：陪伴 衣柜 添加图片 <可选备注>",
                "",
            )
        note = _single_line(argument, 200)
        items = self._wardrobe_items()
        added: list[str] = []
        replaced: list[str] = []
        failures: list[str] = []
        for path, label in images[:limit]:
            parsed, error = await self._wardrobe_describe_image(
                [path],
                note=note,
                umo=_single_line(getattr(event, "unified_msg_origin", ""), 240),
            )
            if parsed is None:
                failures.append(f"{_single_line(label, 80) or '图片'}：{error}")
                continue
            existing = find_wardrobe_item(items, parsed["name"])
            try:
                items, stored = add_wardrobe_item(
                    items,
                    name=parsed["name"],
                    description=parsed["description"],
                    tags=parsed["tags"],
                    source=path,
                    source_kind=SOURCE_KIND_IMAGE,
                )
            except WardrobeLimitError as exc:
                failures.append(str(exc))
                break
            except WardrobeError as exc:
                failures.append(f"{parsed['name']}：{exc}")
                continue
            (replaced if existing else added).append(stored["name"])
        if not added and not replaced:
            detail = "\n".join(failures[:5]) if failures else "没有识别出可用的衣物。"
            return f"没有把衣物加入衣柜：\n{detail}", ""
        if not await self._save_wardrobe_state(items=items):
            return "衣物已识别，但保存失败，请到面板确认配置是否可写。", ""
        lines = []
        if added:
            lines.append("已加入：" + "、".join(added))
        if replaced:
            lines.append("已更新：" + "、".join(replaced))
        if failures:
            lines.append("未处理：" + "；".join(failures[:3]))
        lines.append(f"衣柜现有 {len(items)}/{WARDROBE_MAX_ITEMS} 件。")
        return "\n".join(lines), ""

    async def _wardrobe_update(self, argument: str) -> tuple[str, str]:
        reference, _, description = str(argument or "").partition(" ")
        if not reference or not description.strip():
            return "请这样修改：陪伴 衣柜 修改 <编号或名称> <新描述>", ""
        try:
            items, stored = update_wardrobe_item(
                self._wardrobe_items(), reference, description=description.strip()
            )
        except KeyError:
            return f"衣柜里没有找到「{_single_line(reference, 40)}」。", ""
        except WardrobeError as exc:
            return f"没有修改成功：{exc}", ""
        if not await self._save_wardrobe_state(items=items):
            return "修改没有保存成功，请到面板确认配置是否可写。", ""
        return f"已更新「{stored['name']}」的描述。", ""

    async def _wardrobe_delete(self, argument: str) -> tuple[str, str]:
        reference = str(argument or "").strip()
        if not reference:
            return "请这样删除：陪伴 衣柜 删除 <编号或名称>", ""
        try:
            items, removed = delete_wardrobe_item(self._wardrobe_items(), reference)
        except KeyError:
            return f"衣柜里没有找到「{_single_line(reference, 40)}」。", ""
        if not await self._save_wardrobe_state(items=items):
            return "删除没有保存成功，请到面板确认配置是否可写。", ""
        return f"已从衣柜移除「{removed['name']}」（剩余 {len(items)} 件）。", ""

    async def _wardrobe_clear(self) -> tuple[str, str]:
        empty, _ = clear_wardrobe()
        saved = await self._save_wardrobe_state(items=empty)
        if not saved:
            return "清空没有保存成功，请到面板确认配置是否可写。", ""
        return "已清空角色衣柜的全部衣物（整体服饰倾向保留）。", ""


__all__ = ["WardrobeMixin", "WARDROBE_PROMPT_KEY"]
