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
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _flat_get, _now_ts, _set_into_config, _single_line, _today_key
from .persona_config import PERSONA_SETTINGS_KEY, runtime_persona_setting
from .wardrobe import (
    OUTFIT_KIND_BUNDLE,
    OUTFIT_KIND_STYLE,
    OWNERSHIP_OWNED,
    OWNERSHIP_REFERENCE,
    WARDROBE_IMAGE_KIND_ITEM,
    WARDROBE_IMAGE_KIND_NONE,
    WARDROBE_IMAGE_KIND_OUTFIT,
    WARDROBE_IMAGE_KIND_REFERENCE,
    SOURCE_KIND_IMAGE,
    SOURCE_KIND_MANUAL,
    WARDROBE_MAX_DESCRIPTION,
    WARDROBE_MAX_ITEMS,
    WARDROBE_MAX_NAME,
    WARDROBE_MAX_OUTFITS,
    WARDROBE_MAX_TAG,
    WARDROBE_PROMPT_MAX_CHARS,
    WARDROBE_PROMPT_MAX_ITEMS,
    WARDROBE_PROMPT_PREAMBLE,
    WARDROBE_SLOT_LABELS,
    WardrobeError,
    WardrobeLimitError,
    add_wardrobe_item,
    add_wardrobe_outfit,
    apply_wardrobe_draft,
    build_wardrobe_image_instruction,
    build_wardrobe_outfit_request,
    clear_wardrobe,
    delete_wardrobe_item,
    delete_wardrobe_outfit,
    find_wardrobe_item,
    find_wardrobe_item_by_exact_name,
    find_wardrobe_outfit,
    find_wardrobe_outfit_by_exact_name,
    infer_wardrobe_slot,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_wardrobe_slot,
    normalize_wardrobe_tendency,
    outfit_photo_profile,
    outfit_photo_profile_from_items,
    parse_wardrobe_image_reply,
    parse_wardrobe_outfit_reply,
    render_generated_outfit,
    render_wardrobe_outfit_prompt,
    render_wardrobe_prompt,
    render_worn_items,
    truncate_wardrobe_text,
    select_wardrobe_outfit,
    update_wardrobe_item,
    update_wardrobe_outfit,
    wardrobe_summary_lines,
)
from .wardrobe_assets import (
    ASSET_ORIGIN_BLOGGER,
    ASSET_ORIGIN_LOCAL,
    ASSET_ORIGIN_PANEL,
    ASSET_ORIGIN_SCREENSHOT,
    ASSET_ORIGIN_SHARE_TEXT,
    ASSET_ORIGIN_TAOBAO,
    ASSET_STATUS_IMPORTED,
    ASSET_STATUS_REJECTED,
    ASSET_STATUS_UNDERSTOOD,
    asset_abs_path,
    import_asset,
    list_pending_drafts,
    load_asset_draft,
    load_asset_index,
    mark_asset_status,
)
from .wardrobe_style import render_reference_profile

WARDROBE_PROMPT_KEY = "wardrobe.character"

# 注入详略：full＝每轮都注入完整着装（原有行为）；
# progressive＝常驻只给一行"今天穿什么"，细节等用户问到再展开。
WARDROBE_DETAIL_FULL = "full"
WARDROBE_DETAIL_PROGRESSIVE = "progressive"
WARDROBE_MINIMAL_MAX_CHARS = 200

# 触发词：命中才展开完整描述。让模型自己决定"要不要展开"不可预测，
# 所以触发权收在关键词与命令上（架构 §风险：触发权）。
_WARDROBE_DETAIL_TRIGGERS = (
    "衣服", "穿着", "穿搭", "着装", "外套", "上衣", "衬衫", "毛衣", "卫衣", "开衫",
    "裤子", "裙", "连衣裙", "鞋", "靴", "袜", "围巾", "帽子", "眼镜", "配饰",
    "背包", "包包", "手提包", "挎包", "书包", "单肩包",
    "打扮", "换装", "换衣", "衣柜", "穿什么", "今天穿", "outfit", "wear",
)

# 单字触发词必须带边界：「包」在面包/红包/打包/邮包 里都不是衣服。
# 用负向后顾把它收紧成「前面不像别的词」的裸包，这样「我的包好看吗」仍然命中，
# 而「我想吃面包」「给你发个红包」「帮我打包文件」不再命中。
_WARDROBE_DETAIL_TRIGGER_PATTERNS = (
    re.compile(r"(?<![面红打邮书钱沙纸背钱])(?<![出行背书])包"),
)

# `好看吗` 单独出现太泛（电影/菜/天气都能这么问），必须与穿着语境同现才算问到衣服。
_WARDROBE_DETAIL_CONTEXT_TRIGGERS = (
    "好看吗", "好看不", "怎么样",
)
_WARDROBE_DETAIL_CONTEXT_WORDS = (
    "穿", "衣服", "衣", "裙", "裤", "鞋", "袜", "外套", "搭", "打扮", "造型",
)

# 区分“没有这个值”和“值是 None”，回滚时据此决定是否写回。
_MISSING = object()

# 识图可能较慢；衣柜是显式命令触发的交互，可以等得久一点。
_WARDROBE_VISION_TIMEOUT_SECONDS = 90.0
_WARDROBE_VISION_MAX_IMAGES = 8

# 推理型视觉模型会把 token 预算花在思考过程上，正文可能整个是空的
# （实测 10 张里 4 张如此，finish_reason=length）。空返回时用这句
# 「只要结论」的补语对同一个 Provider 再问一次，仍失败才换下一个候选。
_WARDROBE_VISION_TERSE_SUFFIX = "\n\n请直接输出上面的字段，不要输出思考过程或额外说明。"
# 草稿队列：面板要显示人话，标签映射收在后端一份，别让前端各写一套。
WARDROBE_DRAFT_KIND_LABELS: dict[str, str] = {
    WARDROBE_IMAGE_KIND_ITEM: "散件",
    WARDROBE_IMAGE_KIND_OUTFIT: "整套",
    WARDROBE_IMAGE_KIND_REFERENCE: "参考整套",
    WARDROBE_IMAGE_KIND_NONE: "无法辨认",
}

WARDROBE_ASSET_ORIGIN_LABELS: dict[str, str] = {
    ASSET_ORIGIN_LOCAL: "本地导入",
    ASSET_ORIGIN_PANEL: "面板上传",
    ASSET_ORIGIN_BLOGGER: "博主参考",
    ASSET_ORIGIN_TAOBAO: "淘宝",
    ASSET_ORIGIN_SHARE_TEXT: "分享文本",
    ASSET_ORIGIN_SCREENSHOT: "截图",
}

# 缩略图只对位图有意义；视频帧与分享文本在队列里只显示一行字。
_WARDROBE_DRAFT_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# 按需索取细节的只读工具（在 main.py 用 @filter.llm_tool 注册，这里负责数据与挂载）。
# 名字一旦改了，llm_tool_actions.py 的 known_names 也要跟着改 —— 那张表决定
# 「模型把工具调用当纯文本吐出来」时能不能被识别并从可见回复里剥掉。
WARDROBE_DETAIL_TOOL_NAME = "pc_query_wardrobe_detail"
# 写工具的名字也收在这里：摘除时要读写一起摘，否则同一个开关下两个工具行为不一致。
WARDROBE_INTENT_TOOL_NAME = "pc_set_outfit_intent"

# 本会话明确换装（作者的 dialogue_outfit_override）最多带上几件。
# 上限只是为了把段落长度钉死在 WARDROBE_PROMPT_MAX_CHARS 以内。
WARDROBE_OVERRIDE_MAX_ITEMS = 12

# 「今天穿什么」这条意图**复用作者已有的存储**，不新开 key：
# data["dialogue_outfit_override"] 已经接进 4 个消费点（连续性段落 / 日程调整 /
# scene_context / 状态衰减），core_store 也已登记为可持久化 section。
WARDROBE_INTENT_KEY = "dialogue_outfit_override"
# 过期规则跟作者保持一致：当日 + 12 小时，谁先到算谁。
WARDROBE_INTENT_TTL_SECONDS = 12 * 3600
# 写入来源标记，只用于面板展示（不参与优先级：按约定后写的覆盖先写的）。
WARDROBE_INTENT_SOURCE_MODEL = "model_tool"

# 工具回包的上限：它是「按需展开」，不是把整份衣柜倒给模型，所以比注入段落宽松、
# 但仍有硬上限，避免 40 件长描述把一次工具结果撑成几千字。
WARDROBE_DETAIL_MAX_ITEMS = 40
WARDROBE_DETAIL_MAX_CHARS = 2400
# 只读工具的 scope 取值（含模型常用的中文说法）。不在表里就明确回一句「不认识」，
# 而不是默默按 today 回答 —— 后者会让模型以为自己问的那一份拿到了。
WARDROBE_DETAIL_SCOPES = frozenset(
    {"today", "今天", "今日", "slot", "部位", "all", "全部", "所有", "清单", "inventory"}
)

# 注入段落里那句「可以调用工具」的提示。只在工具真的挂上了这次请求时才拼进去，
# 否则等于让模型调用一个不存在的工具（不支持 function calling 的模型尤其明显）。
WARDROBE_DETAIL_TOOL_HINT = (
    "需要更多细节时可以调用 pc_query_wardrobe_detail（某部位都有什么、今天这身每件是什么）；"
    "不要凭空编造衣柜里没有的衣物。"
)


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

    def _wardrobe_owned_items(self) -> list[dict[str, Any]]:
        """Wearable items only：参考件（ownership=reference）不该被她穿。"""

        return [
            item
            for item in self._wardrobe_items()
            if str(item.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]

    def _wardrobe_owned_outfits(self) -> list[dict[str, Any]]:
        """Wearable outfits only：参考整套只影响风格，不参与"今天穿什么"。"""

        return [
            outfit
            for outfit in self._wardrobe_outfits()
            if str(outfit.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]

    def _wardrobe_references(self) -> list[dict[str, Any]]:
        """Reference looks：只用于归纳风格画像。"""

        return [
            dict(outfit)
            for outfit in self._wardrobe_outfits()
            if str(outfit.get("ownership") or "") == OWNERSHIP_REFERENCE
        ]

    def _wardrobe_reference_profile_line(self) -> str:
        """One-line style profile mined from reference looks ("" when too few)."""

        try:
            return render_reference_profile(self._wardrobe_references())
        except Exception as exc:
            logger.debug("参考风格画像生成失败: %s", _single_line(exc, 160))
            return ""

    def _wardrobe_injection_detail(self) -> str:
        """Return full (每轮完整) or progressive (常驻最小集 + 触发展开)."""

        value = _single_line(
            self._wardrobe_setting("wardrobe_injection_detail", WARDROBE_DETAIL_FULL), 20
        ).casefold()
        if value == WARDROBE_DETAIL_PROGRESSIVE:
            return WARDROBE_DETAIL_PROGRESSIVE
        return WARDROBE_DETAIL_FULL

    @staticmethod
    def _wardrobe_detail_triggered(text: Any) -> bool:
        """True when the inbound message is actually about what she is wearing.

        三重判定：多字触发词裸匹配；单字「包」走带边界的正则；「好看吗/怎么样」这类
        泛化问法必须与穿着语境同现（否则「这电影好看吗」会把 ≤900 字整份清单塞进一轮
        无关对话）。
        """

        haystack = _single_line(text, 400).casefold()
        if not haystack:
            return False
        if any(word.casefold() in haystack for word in _WARDROBE_DETAIL_TRIGGERS):
            return True
        if any(pattern.search(haystack) for pattern in _WARDROBE_DETAIL_TRIGGER_PATTERNS):
            return True
        if any(word in haystack for word in _WARDROBE_DETAIL_CONTEXT_TRIGGERS):
            return any(word in haystack for word in _WARDROBE_DETAIL_CONTEXT_WORDS)
        return False

    def _wardrobe_minimal_body(self, user: Any = None, tendency: Any = "") -> str:
        """常驻最小集：只够让模型知道"今天穿什么"，细节留给触发时展开。"""

        head = "穿着（背景事实，不必主动提）："
        parts: list[str] = []
        if self._wardrobe_outfit_mode() == "select":
            try:
                # 与 select 段落、只读工具同源：意图 > 生成器 > 规则。
                selection = self._wardrobe_resolved_outfit(user)
            except Exception:
                selection = {}
            name = _single_line((selection or {}).get("outfit_name"), 24)
            picked = [
                _single_line(row.get("name"), 16)
                for row in ((selection or {}).get("picked") or ())
            ]
            detail = "、".join(item for item in picked[:4] if item)
            if name:
                parts.append(f"今天穿「{name}」")
            if detail:
                parts.append(detail)
        else:
            clean_tendency = _single_line(tendency, 40)
            if clean_tendency:
                parts.append(f"整体倾向 {clean_tendency}")
            count = len(self._wardrobe_owned_items())
            if count:
                parts.append(f"衣柜 {count} 件")
        if not parts:
            return ""
        body = f"{head}{parts[0]}"
        if len(parts) > 1:
            body += "——" + parts[1]
        body += "。" + chr(10) + "需要细节时再展开；不要复述衣物清单，也不要每轮都提。"
        return body[:WARDROBE_MINIMAL_MAX_CHARS]

    def _wardrobe_outfit_mode(self) -> str:
        """Return inventory (列出全部衣物) or select (只注入裁决出的那一套)."""

        # 兜底与 schema / plugin_bootstrap 的默认值一致（都是 select）。
        text = _single_line(self._wardrobe_setting("wardrobe_outfit_mode", "select"), 20).casefold()
        return "inventory" if text == "inventory" else "select"

    def _wardrobe_outfit_rotation_days(self) -> int:
        """Cooldown window, kept in the same 1..30 range as the author's photo setting."""

        try:
            value = int(self._wardrobe_setting("wardrobe_outfit_rotation_days", 7))
        except (TypeError, ValueError):
            return 7
        return max(1, min(30, value))

    def _wardrobe_current_scene(self) -> str:
        """Reuse the author's scene decision; return "" when it is unavailable.

        Scene is **context**, never a hard filter: it is passed to the outfit
        generator ("what occasion is this?") and mixed into the selection seed
        so different occasions get different outfits. It never removes items
        from the candidate pool -- at home you may well wear swimwear.
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

        seed = self._wardrobe_outfit_seed()
        return select_wardrobe_outfit(
            self._wardrobe_owned_items(),
            self._wardrobe_owned_outfits(),
            scene=self._wardrobe_current_scene(),
            seed=seed,
            rotation_days=self._wardrobe_outfit_rotation_days(),
        )

    def _wardrobe_persona_id(self) -> str:
        for name in ("_active_persona_scope", "_primary_persona_id"):
            getter = getattr(self, name, None)
            if callable(getter):
                persona_id = str(getter() or "").strip()
                if persona_id:
                    return persona_id
        return str(self._wardrobe_setting("plugin_specific_persona_id", "") or "").strip()

    def _wardrobe_outfit_seed(self) -> str:
        return f"{_today_key()}|{self._wardrobe_persona_id()}"

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    async def _save_wardrobe_state(
        self,
        *,
        tendency: str | None = None,
        items: list[dict[str, Any]] | None = None,
        outfits: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Persist wardrobe config atomically; roll back the runtime on failure."""

        payload: dict[str, Any] = {}
        if tendency is not None:
            payload["wardrobe_tendency"] = normalize_wardrobe_tendency(tendency)
        if items is not None:
            payload["wardrobe_items"] = normalize_wardrobe_items(items)
        if outfits is not None:
            payload["wardrobe_outfits"] = normalize_wardrobe_outfits(outfits)
        if not payload:
            return True
        runtime_attr = {
            "wardrobe_tendency": "wardrobe_tendency",
            "wardrobe_items": "wardrobe_items",
            "wardrobe_outfits": "wardrobe_outfits",
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

    def _wardrobe_dialogue_override(self, user: Any = None) -> dict[str, Any]:
        """作者那套「最近一次明确换装」（本会话意图）；取不到就返回 {}。

        只认有身份的私聊用户：作者的连续性段落本身也只在私聊注入，群聊里带上某个人的
        换装会把别人的衣服穿到群里。
        """

        user_id = (
            _single_line((user or {}).get("user_id"), 80)
            if isinstance(user, Mapping)
            else ""
        )
        if not user_id:
            return {}
        return self._wardrobe_override_snapshot(user_id)

    def _wardrobe_override_items(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        """把意图解析成衣柜里的实物：整套优先，其次是散件 id 列表。"""

        items = self._wardrobe_items()
        by_id = {str(item.get("id") or ""): item for item in items}
        picked: list[dict[str, Any]] = []
        seen: set[str] = set()

        def take(raw: Any) -> None:
            key = str(raw or "")
            item = by_id.get(key)
            if item is None or key in seen:
                return
            if str(item.get("ownership") or OWNERSHIP_OWNED) != OWNERSHIP_OWNED:
                # 早期写入可能带上了参考件：渲染时同样不认，免得「参考被穿上身」。
                return
            seen.add(key)
            picked.append(item)

        outfit_id = _single_line(snapshot.get("wardrobe_outfit_id"), 80)
        if outfit_id:
            for outfit in self._wardrobe_outfits():
                if str(outfit.get("id") or "") != outfit_id:
                    continue
                for item_id in outfit.get("items") or ():
                    take(item_id)
                break
        if not picked:
            raw_items = snapshot.get("wardrobe_items")
            if isinstance(raw_items, (list, tuple)):
                for raw in raw_items:
                    take(raw)
        return picked[:WARDROBE_OVERRIDE_MAX_ITEMS]

    def _wardrobe_override_body(
        self, snapshot: Mapping[str, Any], tendency: Any = ""
    ) -> str:
        """本会话明确换装时的段落正文。

        与另外两条渲染路径共用同一段前言（措辞只有一处），但**刻意不出现「当前着装：」**
        这个标题 —— 那个标题的含义是「这是我们裁决出来的那一套」，而这里的一身是用户或
        剧情指定的。
        """

        instruction = _single_line(snapshot.get("instruction"), 180)
        items = self._wardrobe_override_items(snapshot)
        if not instruction and not items:
            return ""
        lines = [WARDROBE_PROMPT_PREAMBLE]
        clean_tendency = normalize_wardrobe_tendency(tendency)
        if clean_tendency:
            lines.append(f"整体服饰倾向：{clean_tendency}")
        lines.append("本会话已经明确换装，当前着装以这次换装为准：")
        if instruction:
            lines.append(f"最近一次明确换装：{instruction}")
        if items:
            lines.append(render_worn_items(items))
        else:
            lines.append(
                "衣柜清单里没有完全对应的衣物：按剧情临时服装处理，"
                "不要用清单里的默认搭配把它换回来，也不要声称它出自衣柜。"
            )
        # 整个段落（含前言）封顶：override 路径原先没有任何上限，12 件长描述实测能到 1908。
        return truncate_wardrobe_text(chr(10).join(lines), WARDROBE_PROMPT_MAX_CHARS)

    def _wardrobe_override_snapshot(self, user_id: str = "") -> dict[str, Any]:
        """原样读作者那套 override（不做身份过滤）；取不到返回 {}。"""

        getter = getattr(self, "_current_dialogue_outfit_override", None)
        if not callable(getter):
            return {}
        try:
            snapshot = getter(user_id=user_id)
        except TypeError:
            # 宿主签名可能只接受位置参数；退化成不带身份读取。
            try:
                snapshot = getter()
            except Exception:
                return {}
        except Exception:
            return {}
        return dict(snapshot) if isinstance(snapshot, Mapping) else {}

    def _wardrobe_intent_snapshot(self, user: Any = None) -> dict[str, Any]:
        """给面板/工具看的「本会话已明确换装」：原始字段 + 解析出的实物。

        面板没有用户身份，所以这里**不做身份过滤**（读的是同一个人格下的那一份）。
        """

        snapshot = self._wardrobe_override_snapshot()
        if not snapshot:
            return {}
        items = self._wardrobe_override_items(snapshot)
        outfit_id = _single_line(snapshot.get("wardrobe_outfit_id"), 80)
        outfit_name = ""
        if outfit_id:
            for outfit in self._wardrobe_outfits():
                if str(outfit.get("id") or "") == outfit_id:
                    outfit_name = str(outfit.get("name") or "")
                    break
        return {
            "instruction": _single_line(snapshot.get("instruction"), 180),
            "source": _single_line(snapshot.get("source"), 40),
            "date": _single_line(snapshot.get("date"), 16),
            "created_at": snapshot.get("created_at"),
            "expires_at": snapshot.get("expires_at"),
            "outfit_id": outfit_id,
            "outfit_name": outfit_name,
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "slot": str(item.get("slot") or ""),
                }
                for item in items
            ],
        }

    @staticmethod
    def _wardrobe_intent_tokens(raw: Any) -> list[str]:
        """把工具传来的那一串名称/id 拆开：逗号、顿号、斜杠、分号、换行都算分隔符。"""

        # 模型可能把 items 传成数组（AstrBot 只按参数名过滤、不做类型校验），
        # 直接 _single_line(list) 会得到 "['分体泳衣上装']" 这种乱码，解析必然失败
        # 却仍然回 ok=true —— 那会让提示词说「衣柜里没有这件」。先展开成文本。
        if isinstance(raw, (list, tuple, set)):
            parts: list[str] = []
            for entry in raw:
                if isinstance(entry, Mapping):
                    parts.append(str(entry.get("name") or entry.get("id") or ""))
                else:
                    parts.append(str(entry or ""))
            raw = "，".join(parts)
        text = _single_line(raw, 600)
        if not text:
            return []
        tokens: list[str] = []
        for chunk in text.replace("，", ",").replace("、", ",").replace("/", ",").replace("；", ",").replace(";", ",").split(","):
            token = _single_line(chunk, 60)
            if token and token not in tokens:
                tokens.append(token)
        return tokens[:WARDROBE_OVERRIDE_MAX_ITEMS]

    def _wardrobe_resolve_intent(
        self, items: Any = "", outfit: Any = ""
    ) -> tuple[list[dict[str, Any]], list[str], str, str]:
        """把工具传来的名称/id 解析成衣柜实物。

        返回 (命中散件, 未命中的名字, 命中整套 id, 命中整套名)。解析规则刻意保守：
        只认 id、完整名称、以及**唯一**的子串命中 —— 挑错衣服比挑不到更糟。
        """

        # 只在**自有**散件里解析：ownership=reference 是别人的穿搭灵感，
        # 不该被点名成「正在穿」（与 _wardrobe_owned_items 的声明一致）。
        wardrobe_items = self._wardrobe_owned_items()
        by_id = {str(row.get("id") or ""): row for row in wardrobe_items}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in wardrobe_items:
            key = str(row.get("name") or "").strip().casefold()
            if key:
                by_name.setdefault(key, []).append(row)
        # 整套同样只用**自有**的：参考整套是别人的穿搭灵感，被点名成
        # 「她换成了这套」会污染作者的连续性段落与日程调整（与散件路径同规格）。
        outfit_rows = self._wardrobe_owned_outfits()
        outfits_by_id = {str(row.get("id") or ""): row for row in outfit_rows}
        outfits_by_name: dict[str, dict[str, Any]] = {}
        for row in outfit_rows:
            key = str(row.get("name") or "").strip().casefold()
            if key:
                outfits_by_name.setdefault(key, row)

        picked: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for token in self._wardrobe_intent_tokens(items):
            row = by_id.get(token)
            if row is None:
                # 同名两件必须判未命中：与子串歧义同一套「挑错衣服比挑不到更糟」。
                same_name = by_name.get(token.casefold()) or []
                row = same_name[0] if len(same_name) == 1 else None
            if row is None:
                needle = token.casefold()
                matches = [
                    candidate
                    for candidate in wardrobe_items
                    if needle and needle in str(candidate.get("name") or "").casefold()
                ]
                row = matches[0] if len(matches) == 1 else None
            if row is None:
                unresolved.append(token)
                continue
            if row not in picked:
                picked.append(row)

        outfit_id = ""
        outfit_name = ""
        clean_outfit = _single_line(outfit, 80)
        if clean_outfit:
            row = outfits_by_id.get(clean_outfit) or outfits_by_name.get(clean_outfit.casefold())
            if row is None:
                unresolved.append(clean_outfit)
            else:
                outfit_id = str(row.get("id") or "")
                outfit_name = str(row.get("name") or "")
        return picked, unresolved, outfit_id, outfit_name

    def _schedule_wardrobe_intent_save(self) -> None:
        """把意图落盘。section 名必须是 core_store 登记过的那个，否则直接抛错。"""

        saver = getattr(self, "_schedule_data_save", None)
        if not callable(saver):
            return
        try:
            saver(sections={WARDROBE_INTENT_KEY})
        except Exception as exc:
            logger.warning("穿衣意图落盘失败: %s", _single_line(exc, 160))

    def _wardrobe_set_intent(
        self, intent: Any = "", *, items: Any = "", outfit: Any = "", user: Any = None
    ) -> dict[str, Any]:
        """把「本会话要穿什么」写进作者那套 dialogue_outfit_override。

        与作者的正则写同一个 key、同一套过期规则（当日 + 12h），不新建存储 ——
        作者的三个消费点（连续性段落 / 日程调整 / scene_context）因此自动生效。
        按约定，后写的覆盖先写的，source 只用于面板展示。
        """

        outcome: dict[str, Any] = {
            "ok": False,
            "instruction": "",
            "source": WARDROBE_INTENT_SOURCE_MODEL,
            "resolved": [],
            "unresolved": [],
            "outfit": "",
            "error": "",
        }
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            outcome["error"] = "当前运行时不支持记录穿衣意图。"
            return outcome
        # 总开关关掉时读写两个工具必须一致：读工具已被按请求摘掉，写工具若不拦，
        # 管理员关掉衣柜后模型仍能改角色着装（还会写进作者的连续性段落）。
        if not self._wardrobe_enabled():
            outcome["error"] = "角色衣柜没有启用。"
            return outcome
        # 门禁：作者那条写同一个 key 的路径只认主要用户（daily_state 里
        # `_private_user_role(user) != "owner"` 直接返回）。这条 key 是全局的
        # （生图与面板读的是不带用户过滤的那一份），放任任何人群里喊一句就覆盖，
        # 等于把主人的意图静默清空、让照片按别人的要求穿。**取不到角色判定时一律拒绝**。
        role_getter = getattr(self, "_private_user_role", None)
        role = ""
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user), 24)
            except Exception:
                role = ""
        if role != "owner":
            logger.info("衣柜穿衣意图写入被拒（非主要用户）: role=%s", role or "unknown")
            outcome["error"] = "只有主要用户可以改变角色今天的着装。"
            return outcome
        picked, unresolved, outfit_id, outfit_name = self._wardrobe_resolve_intent(items, outfit)
        clean_intent = _single_line(intent, 180)
        if not clean_intent and not picked and not outfit_id:
            outcome["error"] = "没有可记录的换装内容：给出想换上的衣物名称，或一句换装描述。"
            return outcome
        if not clean_intent:
            names = "、".join(str(row.get("name") or "") for row in picked) or outfit_name
            clean_intent = f"换上{names}" if names else "换装"
        user_id = (
            _single_line((user or {}).get("user_id"), 80)
            if isinstance(user, Mapping)
            else ""
        )
        now = _now_ts()
        snapshot = {
            "date": _today_key(),
            "instruction": clean_intent,
            "source": WARDROBE_INTENT_SOURCE_MODEL,
            "source_user_id": user_id,
            "created_at": now,
            "expires_at": now + WARDROBE_INTENT_TTL_SECONDS,
            "wardrobe_items": [str(row.get("id") or "") for row in picked],
            "wardrobe_outfit_id": outfit_id,
        }
        data[WARDROBE_INTENT_KEY] = snapshot
        self._schedule_wardrobe_intent_save()
        outcome.update(
            {
                "ok": True,
                "instruction": clean_intent,
                "resolved": [str(row.get("name") or "") for row in picked],
                "unresolved": unresolved,
                "outfit": outfit_name,
                "expires_at": snapshot["expires_at"],
            }
        )
        return outcome

    def _wardrobe_clear_intent(self) -> bool:
        """清掉本会话的换装意图（面板纠正用）；没有就返回 False。"""

        data = getattr(self, "data", None)
        if not isinstance(data, dict) or not data.get(WARDROBE_INTENT_KEY):
            return False
        data[WARDROBE_INTENT_KEY] = {}
        self._schedule_wardrobe_intent_save()
        return True

    def _wardrobe_intent_user(self, event: Any) -> dict[str, Any]:
        """从事件里定位当前用户：意图要写给他本人，而不是「当前人格的某个人」。"""

        getter = getattr(self, "_event_sender_id", None)
        user_id = ""
        if callable(getter):
            try:
                user_id = _single_line(getter(event), 80)
            except Exception:
                user_id = ""
        if not user_id:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
        loader = getattr(self, "_get_user", None)
        if user_id and callable(loader):
            try:
                record = loader(user_id)
            except Exception:
                record = None
            if isinstance(record, Mapping):
                return dict(record)
        return {"user_id": user_id} if user_id else {}

    def _wardrobe_intent_reply(
        self, intent: Any = "", *, items: Any = "", outfit: Any = "", user: Any = None
    ) -> str:
        """工具返回值：JSON 字符串（宿主以 role:"tool" 回灌给模型）。"""

        try:
            outcome = self._wardrobe_set_intent(intent, items=items, outfit=outfit, user=user)
        except Exception as exc:
            logger.warning("记录穿衣意图失败: %s", _single_line(exc, 160))
            return json.dumps(
                {"status": "error", "message": "记录穿衣意图失败。"}, ensure_ascii=False
            )
        return json.dumps(outcome, ensure_ascii=False)

    def _wardrobe_prompt_section(
        self, user: Any = None, text: Any = "", *, detail_tool: bool = False
    ) -> PromptSection | None:
        """Build the wardrobe prompt section, or ``None`` when it should not inject.

        text 是本轮用户消息：渐进披露模式下用它判断"要不要展开完整描述"。

        detail_tool 为真时在段落末尾追加一句「可以调用 pc_query_wardrobe_detail」——
        由调用方先挂好工具再传进来，避免提示词里写了一件这次请求根本没有的工具。
        """

        if not self._wardrobe_enabled() or not self._wardrobe_prompt_mode():
            return None
        tendency = self._wardrobe_tendency()
        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        if not tendency and not items and not outfits:
            return None
        progressive = self._wardrobe_injection_detail() == WARDROBE_DETAIL_PROGRESSIVE
        # 本会话已经明确换装时整段以它为准：绝不能再把轮换裁决出的那一套标成
        # 「当前着装」，否则同一轮提示词里会出现两段互相矛盾的说法 —— 作者的
        # 连续性段落说「最近一次明确换装：泳衣…不得自行恢复旧服装」，我们这边
        # 却写着「当前着装：短裤」。
        body = self._wardrobe_override_body(
            self._wardrobe_dialogue_override(user), tendency
        )
        # 注意这里是**嵌套 if 不是 elif 链**：最小集可能因为「衣柜里全是参考件」返回空串，
        # 用 elif 会被短路成 return None —— 整段衣柜（连参考风格画像）静默消失。
        if not body and progressive and not self._wardrobe_detail_triggered(text):
            # 常驻最小集：每轮都发，但很短；细节等触发。
            body = self._wardrobe_minimal_body(user, tendency)
        if not body and self._wardrobe_outfit_mode() == "select":
            body = self._wardrobe_selected_outfit_body(user, tendency)
        if not body:
            body = render_wardrobe_prompt(
                tendency,
                items,
                max_items=self._wardrobe_prompt_item_limit(),
                max_chars=WARDROBE_PROMPT_MAX_CHARS,
            )
        if not body:
            return None
        # 参考风格画像：把大量参考压成一行，只在放得下时才追加
        # （整套本身比风格画像重要，放不下就牺牲画像）。
        profile_line = self._wardrobe_reference_profile_line()
        if profile_line and len(body) + len(profile_line) + 1 <= WARDROBE_PROMPT_MAX_CHARS:
            body = f"{body}\n{profile_line}"
        # 工具提示排最后：放不下就牺牲它（工具本身还在，只是模型不知道，
        # 退化成现在这套「关键词触发展开」的行为）。
        if detail_tool and len(body) + len(WARDROBE_DETAIL_TOOL_HINT) + 1 <= WARDROBE_PROMPT_MAX_CHARS:
            body = f"{body}\n{WARDROBE_DETAIL_TOOL_HINT}"
        return prompt_section(
            key=WARDROBE_PROMPT_KEY,
            title="角色衣柜",
            source="wardrobe",
            content=body,
        )

    def _wardrobe_resolved_outfit(self, user: Any = None) -> dict[str, Any]:
        """「今天这一身」的**唯一**解析入口：本会话意图 > 生成器缓存 > 规则裁决。

        段落（select 模式）、渐进披露最小集、只读工具的 today、面板预览都必须走这里。
        否则同一天会出现多套答案 —— 实测：生成器开启时提示词按缓存写「奶油色针织开衫」，
        模型照段落里那句提示去调工具，拿回的是规则裁决的「白衬衫黑纱裙」。

        返回值与 :func:`select_wardrobe_outfit` 同形，另外在 override 时多一个
        ``instruction``（意图原文），供最小集与工具复用。
        """

        override = self._wardrobe_dialogue_override(user)
        if override:
            items = self._wardrobe_override_items(override)
            return {
                "source": "dialogue_override",
                "scene": self._wardrobe_current_scene(),
                "style": "",
                "prompt_text": render_worn_items(items),
                "profile": outfit_photo_profile_from_items(items),
                "picked": [
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "slot": str(item.get("slot") or ""),
                        "intimate": bool(item.get("intimate")),
                    }
                    for item in items
                ],
                "look_id": "",
                "outfit_name": "",
                "instruction": _single_line(override.get("instruction"), 180),
            }
        generated = self._wardrobe_cached_generated_outfit()
        if generated is not None:
            return self._wardrobe_generated_selection(generated, self._wardrobe_current_scene())
        return self._wardrobe_outfit_selection(user)

    def _wardrobe_selected_outfit_body(self, user: Any = None, tendency: Any = "") -> str:
        """Body for the select mode: only the resolved outfit, not the inventory.

        Falls back to the full listing when nothing can be resolved (an empty
        wardrobe, or no item carrying a usable slot) so the section never
        silently becomes empty.
        """

        selection = self._wardrobe_resolved_outfit(user)
        if selection.get("source") == "dialogue_override":
            # 意图段落由 _wardrobe_override_body 负责（它要带 instruction 行，且不能出现
            # 「当前着装：」标题）；走到这里说明调用方绕过了那条路，交回清单更安全。
            return ""
        if selection.get("source") == "rule":
            # 同步路径不能等模型：只有落到规则裁决时才排后台生成，下一轮就能用上结果。
            self._schedule_wardrobe_outfit_generation(user)
        body = render_wardrobe_outfit_prompt(tendency, selection)
        if body:
            return body
        return render_wardrobe_prompt(
            tendency,
            self._wardrobe_items(),
            max_items=self._wardrobe_prompt_item_limit(),
            max_chars=WARDROBE_PROMPT_MAX_CHARS,
        )


    # ------------------------------------------------------------------
    # 生成器（模型路径）
    # ------------------------------------------------------------------

    def _wardrobe_generator_enabled(self) -> bool:
        return self._wardrobe_bool(
            self._wardrobe_setting("enable_wardrobe_outfit_generate", False), False
        )

    def _wardrobe_generator_provider_id(self) -> str:
        return _single_line(self._wardrobe_setting("WARDROBE_OUTFIT_PROVIDER_ID", ""), 160)

    def _wardrobe_current_weather(self) -> str:
        getter = getattr(self, "_format_weather_for_prompt", None)
        if not callable(getter):
            return ""
        try:
            return _single_line(getter(), 120)
        except Exception:
            return ""

    def _wardrobe_outfit_cache_key(self) -> str:
        """Keep each persona's daily result tied to the complete wardrobe."""

        def content(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {key: value for key, value in row.items() if key not in {"created_at", "updated_at"}}
                for row in rows
            ]

        inputs = {
            "persona": self._wardrobe_persona_id(),
            "scene": self._wardrobe_current_scene(),
            "weather": self._wardrobe_current_weather(),
            "items": content(self._wardrobe_items()),
            "outfits": content(self._wardrobe_outfits()),
            "tendency": self._wardrobe_tendency(),
            "provider": self._wardrobe_generator_provider_id(),
            "prompt": self._wardrobe_setting("task_prompt_overrides", {}),
        }
        digest = hashlib.sha256(
            json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"{_today_key()}|{digest}"

    def _wardrobe_outfit_cache(self) -> dict[str, Any]:
        """In-memory cache for generated outfits.

        Deliberately in memory: persisting it would need a new data key in the
        author's store, which this change set avoids. The consequence is that a
        plugin restart on the same day regenerates the outfit once.
        """

        cache = getattr(self, "_wardrobe_outfit_cache_store", None)
        if not isinstance(cache, dict):
            cache = {}
            self._wardrobe_outfit_cache_store = cache
        return cache

    def _wardrobe_cached_generated_outfit(self) -> dict[str, Any] | None:
        if not self._wardrobe_generator_enabled():
            return None
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        cached = cache.get(self._wardrobe_outfit_cache_key()) if isinstance(cache, dict) else None
        return cached if isinstance(cached, dict) and cached else None

    @staticmethod
    def _wardrobe_generated_selection(generated: dict[str, Any], scene: str) -> dict[str, Any]:
        return {
            "source": "generate",
            "scene": scene,
            "prompt_text": render_generated_outfit(generated),
            "profile": outfit_photo_profile(generated),
            "picked": [],
        }

    def _schedule_wardrobe_outfit_generation(self, user: Any = None) -> None:
        """Kick off generation in the background so the reply path never waits.

        The prompt-section builder is synchronous and sits on the reply path, so
        generating inline would add a model round-trip to the user's first
        message of the day. Instead the current turn uses the rule selection and
        the generated outfit is picked up from the next turn onward.
        """

        if not self._wardrobe_generator_enabled():
            return
        key = self._wardrobe_outfit_cache_key()
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        if isinstance(cache, dict) and key in cache:
            return
        runner = getattr(self, "_create_lifecycle_background_task", None)
        if not callable(runner):
            return
        tasks = getattr(self, "_wardrobe_generation_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._wardrobe_generation_tasks = tasks
        previous = tasks.get(key)
        if previous is not None and not previous.done():
            return
        operation = self._wardrobe_generate_outfit(user)
        try:
            task = runner(operation, label="wardrobe_outfit_generate")
        except Exception as exc:
            operation.close()
            logger.debug("着装生成任务调度失败: %s", _single_line(exc, 160))
            return
        if task is None:
            operation.close()
            return
        tasks[key] = task

        def discard(finished: asyncio.Task) -> None:
            if tasks.get(key) is finished:
                tasks.pop(key, None)

        task.add_done_callback(discard)

    async def _wardrobe_generate_outfit(self, user: Any = None) -> dict[str, Any] | None:
        """Ask the model to compose the outfit for the current (date, scene).

        Returns None when generation is disabled, unavailable, or produced
        nothing usable -- callers then fall back to the rule selector, so a
        model outage degrades instead of injecting an empty outfit.
        """

        if not self._wardrobe_generator_enabled():
            return None
        caller = getattr(self, "_llm_call", None)
        if not callable(caller):
            return None
        key = self._wardrobe_outfit_cache_key()
        cache = self._wardrobe_outfit_cache()
        if key in cache:
            cached = cache[key]
            return cached if isinstance(cached, dict) and cached else None

        request = build_wardrobe_outfit_request(
            self._wardrobe_items(),
            self._wardrobe_outfits(),
            tendency=self._wardrobe_tendency(),
            scene=self._wardrobe_current_scene(),
            weather=self._wardrobe_current_weather(),
        )
        payload: dict[str, Any] | None = None
        try:
            raw = await caller(
                request,
                max_tokens=700,
                provider_id=self._wardrobe_generator_provider_id() or None,
                task="wardrobe_outfit_generate",
            )
            payload = parse_wardrobe_outfit_reply(raw)
        except Exception as exc:
            logger.warning("着装生成调用失败: %s", _single_line(exc, 160))
            payload = None
        # 失败也缓存：同一天内不再反复重试，直接走规则选择器降级。
        today = _today_key() + "|"
        for stale in list(cache):
            if not stale.startswith(today):
                cache.pop(stale, None)
        while len(cache) >= 128:
            cache.pop(next(iter(cache)))
        cache[key] = payload or {}
        if payload is None:
            logger.info("着装生成未产出可用结果，本次回退规则挑选")
        return payload

    def _wardrobe_outfit_preview(
        self,
        *,
        scene: Any = None,
        weather: Any = None,
        seed: Any = "",
    ) -> dict[str, Any]:
        """Diagnostics for the 搭配测试 panel: what would be injected, and why.

        Strictly read-only -- it never writes config, never calls the model and
        never touches the generation cache, so the panel can be refreshed freely.
        Passing scene/weather overrides the auto-detected ones so the panel can
        simulate other occasions without waiting for the real schedule to change.
        """

        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        tendency = self._wardrobe_tendency()
        clean_scene = (
            self._wardrobe_current_scene() if scene is None else _single_line(scene, 20)
        )
        clean_weather = (
            self._wardrobe_current_weather() if weather is None else _single_line(weather, 120)
        )
        clean_seed = _single_line(seed, 160) or self._wardrobe_outfit_seed()
        mode = self._wardrobe_outfit_mode()

        owned_items = [
            item for item in items
            if str(item.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]
        owned_outfits = [
            outfit for outfit in outfits
            if str(outfit.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]
        references = [
            outfit for outfit in outfits
            if str(outfit.get("ownership") or "") == OWNERSHIP_REFERENCE
        ]
        selection = select_wardrobe_outfit(
            owned_items,
            owned_outfits,
            scene=clean_scene,
            seed=clean_seed,
            rotation_days=self._wardrobe_outfit_rotation_days(),
        )
        current_context = (
            clean_scene == self._wardrobe_current_scene()
            and clean_weather == self._wardrobe_current_weather()
            and clean_seed == self._wardrobe_outfit_seed()
        )
        generated = self._wardrobe_cached_generated_outfit() if current_context and mode == "select" else None
        generated_view: dict[str, Any] | None = None
        if generated is not None:
            generated_view = {
                "fields": dict(generated),
                "body": render_generated_outfit(generated),
                "profile": outfit_photo_profile(generated),
            }
        selected = (
            self._wardrobe_generated_selection(generated, clean_scene)
            if generated is not None else selection
        )
        # 预览必须与真实注入同源：意图 > 生成器 > 规则，并且要认渐进披露。
        # 预览没有「本轮消息」，渐进披露按「未触发」算 —— 也就是那个常驻最小集。
        # 面板没有用户身份，意图读的是不带过滤的那一份（与 /wardrobe/intent 一致）。
        override = self._wardrobe_override_snapshot()
        effective_source = ""
        injected = ""
        if self._wardrobe_enabled() and self._wardrobe_prompt_mode():
            if override:
                injected = self._wardrobe_override_body(override, tendency)
                effective_source = "dialogue_override"
            elif self._wardrobe_injection_detail() == WARDROBE_DETAIL_PROGRESSIVE:
                injected = self._wardrobe_minimal_body(None, tendency)
                effective_source = "minimal"
            else:
                effective_source = "generated" if generated is not None else "rule"
            if not injected and mode == "select":
                injected = render_wardrobe_outfit_prompt(tendency, selected)
            if not injected:
                injected = render_wardrobe_prompt(
                    tendency, items, max_items=self._wardrobe_prompt_item_limit(),
                    max_chars=WARDROBE_PROMPT_MAX_CHARS,
                )
                effective_source = effective_source or "inventory"
        key = self._wardrobe_outfit_cache_key()
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        tasks = getattr(self, "_wardrobe_generation_tasks", {})
        pending = tasks.get(key) if isinstance(tasks, dict) and current_context else None
        generator_state = (
            "disabled" if not self._wardrobe_generator_enabled() or mode != "select"
            else "ready" if generated is not None
            else "pending" if pending is not None and not pending.done()
            else "failed" if current_context and isinstance(cache, dict) and key in cache
            else "idle"
        )
        return {
            "mode": mode,
            "enabled": self._wardrobe_enabled() and self._wardrobe_prompt_mode(),
            "generator_enabled": self._wardrobe_generator_enabled(),
            "generator_ready": generated is not None,
            "generator_state": generator_state,
            "scene": clean_scene,
            "weather": clean_weather,
            "seed": clean_seed,
            "item_count": len(items),
            "outfit_count": len(outfits),
            "reference_count": len(references),
            "style_profile": render_reference_profile(references),
            # 未分类的衣物没有部位可依据，正常不参与组合；数量暴露给面板，方便
            # 提示用户补全，而不是让他纳闷"为什么这几件从来不出现"。
            "unclassified_count": len(
                [item for item in items if not str(item.get("slot") or "")]
            ),
            "request": build_wardrobe_outfit_request(
                owned_items,
                outfits,
                tendency=tendency,
                scene=clean_scene,
                weather=clean_weather,
            ),
            "rule": {
                "source": str(selected.get("source") or ""),
                "outfit_name": str(selected.get("outfit_name") or ""),
                "look_id": str(selected.get("look_id") or ""),
                "picked": [dict(row) for row in (selected.get("picked") or ())],
                "profile": dict(selected.get("profile") or {}),
                "prompt_text": str(selected.get("prompt_text") or ""),
            },
            "generated": generated_view,
            "selected": selected,
            "injected": injected,
            "injected_chars": len(injected),
            "injected_limit": WARDROBE_PROMPT_MAX_CHARS,
            # 这次注入实际由哪条路径决定（意图 / 生成器 / 规则 / 最小集 / 清单），
            # 让面板能解释「为什么注入的是这一份」。
            "effective_source": effective_source,
            # 渐进披露对比：面板可以直接把"常驻最小集"与完整注入并排显示
            "detail_mode": self._wardrobe_injection_detail(),
            "minimal": self._wardrobe_minimal_body(None, tendency),
            "minimal_limit": WARDROBE_MINIMAL_MAX_CHARS,
        }

    async def _append_group_wardrobe_to_request(self, event: Any, req: Any) -> None:
        """Inject the wardrobe into a group request exactly once."""

        if req is None:
            return
        # 先把只读工具按请求挂好，再决定提示词里要不要写它 —— 顺序不能反，
        # 否则会出现「提示词说有、工具表里没有」。
        syncer = getattr(self, "_sync_wardrobe_detail_tool", None)
        detail_tool = False
        if callable(syncer):
            try:
                detail_tool = bool(syncer(req))
            except Exception as exc:
                logger.debug("群聊衣柜细节工具挂载失败: %s", _single_line(exc, 160))
        builder = getattr(self, "_wardrobe_prompt_section", None)
        if not callable(builder):
            return
        try:
            section = builder(None, detail_tool=detail_tool)
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
    # 按需索取：只读工具
    # ------------------------------------------------------------------

    def _wardrobe_detail_available(self) -> bool:
        """这次请求查得到东西吗：衣柜启用、注入开启、且衣柜非空。"""

        if not self._wardrobe_enabled() or not self._wardrobe_prompt_mode():
            return False
        return bool(self._wardrobe_items() or self._wardrobe_outfits())

    @staticmethod
    def _wardrobe_detail_lines(
        items: Any, *, limit_items: int, limit_chars: int
    ) -> tuple[list[str], bool]:
        """把条目渲染成「序号. [部位] 名称（标签）：描述」，条数与字数双重封顶。"""

        lines: list[str] = []
        used = 0
        truncated = False
        for index, item in enumerate(items or (), start=1):
            if limit_items and len(lines) >= limit_items:
                truncated = True
                break
            detail = _single_line(item.get("description"), WARDROBE_MAX_DESCRIPTION)
            tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
            if item.get("intimate"):
                tags = ["贴身", *tags]
            slot = str(item.get("slot") or "")
            slot_label = WARDROBE_SLOT_LABELS.get(slot, "未分类")
            suffix = f"（{'/'.join(tags)}）" if tags else ""
            line = f"{index}. [{slot_label}] {item.get('name') or ''}{suffix}"
            if detail:
                line = f"{line}：{detail}"
            if limit_chars and used + len(line) + 1 > limit_chars:
                truncated = True
                break
            used += len(line) + 1
            lines.append(line)
        return lines, truncated

    def _wardrobe_detail_payload(
        self, scope: Any = "today", slot: Any = "", user: Any = None
    ) -> dict[str, Any]:
        """只读地组装「模型按需索取」的衣柜细节。

        三种 scope：

        * today —— 今天裁决出的那一套（逐件名称与描述）；
        * slot  —— 指定部位的全部衣物（需要 slot 参数）；
        * all   —— 整份衣柜清单（散件 + 整套 + 参考风格画像）。

        全程只读：不写配置、不调模型、不推进任何状态，重复调用结果一致。
        """

        clean_scope = _single_line(scope, 16).casefold() or "today"
        clean_slot = normalize_wardrobe_slot(_single_line(slot, 24))
        payload: dict[str, Any] = {
            "status": "ok",
            "scope": clean_scope,
            "slot": clean_slot,
            "text": "",
            "truncated": False,
        }
        if not self._wardrobe_detail_available():
            payload["status"] = "unavailable"
            payload["text"] = "角色衣柜当前没有启用，或衣柜里还没有衣物。"
            return payload
        if clean_scope not in WARDROBE_DETAIL_SCOPES:
            payload["status"] = "unknown_scope"
            payload["text"] = (
                "scope 只支持 today（今天这身）/ slot（指定部位）/ all（整份衣柜）。"
            )
            return payload
        items = self._wardrobe_items()
        if clean_scope in {"slot", "部位"}:
            if not clean_slot:
                payload["status"] = "need_slot"
                payload["text"] = (
                    "请给出部位：upper 上装 / lower 下装 / whole 整身 / feet 鞋 / extra 配件。"
                )
                return payload
            # 只列**自有**散件：参考件是别人的穿搭灵感，列进「某部位都有什么」
            # 会让模型以为她拥有并可穿（与 scope=all 的口径保持一致）。
            rows = [
                item
                for item in self._wardrobe_owned_items()
                if str(item.get("slot") or "") == clean_slot
            ]
            label = WARDROBE_SLOT_LABELS.get(clean_slot, clean_slot)
            payload["count"] = len(rows)
            if not rows:
                payload["text"] = f"{label}：还没有衣物。"
                return payload
            lines, truncated = self._wardrobe_detail_lines(
                rows, limit_items=WARDROBE_DETAIL_MAX_ITEMS, limit_chars=WARDROBE_DETAIL_MAX_CHARS
            )
            payload["text"] = f"{label}（共 {len(rows)} 件）：\n" + "\n".join(lines)
            payload["truncated"] = truncated
            return payload
        if clean_scope in {"all", "全部", "清单", "inventory"}:
            owned = self._wardrobe_owned_items()
            lines, truncated = self._wardrobe_detail_lines(
                owned, limit_items=WARDROBE_DETAIL_MAX_ITEMS, limit_chars=WARDROBE_DETAIL_MAX_CHARS
            )
            # 表头、正文、count 三者必须同一口径（都是自有件）：此前表头按全部计数、
            # 正文只列自有件，模型会照表头声称她拥有参考件。
            parts = [
                f"衣柜共 {len(owned)} 件可穿散件 / {len(self._wardrobe_owned_outfits())} 套整套。"
            ]
            tendency = self._wardrobe_tendency()
            if tendency:
                parts.append(f"整体服饰倾向：{tendency}")
            if lines:
                parts.append("可穿散件：\n" + "\n".join(lines))
            outfits = self._wardrobe_owned_outfits()
            names = [str(row.get("name") or "") for row in outfits if str(row.get("name") or "")]
            # 整套名单此前不参与预算，30 套长名字实测能把回包撑到 3184 字 ——
            # 逐条累加，超预算就停下并标 truncated。
            if names:
                budget = WARDROBE_DETAIL_MAX_CHARS - sum(len(part) + 1 for part in parts)
                kept: list[str] = []
                for name in names:
                    if len("、".join((*kept, name))) + 3 > budget:
                        truncated = True
                        break
                    kept.append(name)
                if kept:
                    suffix = "…" if len(kept) < len(names) else ""
                    parts.append("整套：" + "、".join(kept) + suffix)
            profile_line = self._wardrobe_reference_profile_line()
            if profile_line and len(profile_line) + 1 <= WARDROBE_DETAIL_MAX_CHARS - sum(len(p) + 1 for p in parts):
                parts.append(profile_line)
            payload["count"] = len(owned)
            payload["text"] = "\n".join(parts)
            payload["truncated"] = truncated
            return payload
        # 与提示词**同源**：走同一个解析入口（意图 > 生成器 > 规则）。
        # 只补 override 分支是不够的 —— 生成器开启时提示词按缓存渲染，工具却报规则裁决，
        # 模型照段落提示来问一次就被带回另一套衣服。
        selection = self._wardrobe_resolved_outfit(user)
        if selection.get("source") == "dialogue_override":
            override_items = self._wardrobe_override_items(
                self._wardrobe_dialogue_override(user)
            )
            override = {"instruction": selection.get("instruction")}
            override_parts: list[str] = []
            instruction = _single_line(override.get("instruction"), 180)
            if instruction:
                override_parts.append(f"本会话已明确换装：{instruction}")
            override_lines, override_truncated = self._wardrobe_detail_lines(
                override_items,
                limit_items=WARDROBE_DETAIL_MAX_ITEMS,
                limit_chars=WARDROBE_DETAIL_MAX_CHARS,
            )
            if override_lines:
                override_parts.append("当前这身：" + chr(10) + chr(10).join(override_lines))
            else:
                override_parts.append(
                    "衣柜清单里没有完全对应的衣物：按剧情临时服装处理，"
                    "不要用清单里的默认搭配换回来。"
                )
            payload["count"] = len(override_items)
            payload["text"] = chr(10).join(override_parts)
            payload["truncated"] = override_truncated
            payload["source"] = "dialogue_override"
            return payload
        # 注意：**不要**在这里重新调 _wardrobe_outfit_selection —— 那会把上面解析出的
        # 生成器结果覆盖掉，正是「提示词按缓存渲染、工具报规则裁决」的根因。
        picked_ids = {str(row.get("id") or "") for row in (selection.get("picked") or ())}
        picked = [item for item in items if str(item.get("id") or "") in picked_ids]
        parts = []
        scene = _single_line(self._wardrobe_current_scene(), 40)
        if scene:
            parts.append(f"今天的场合：{scene}")
        tendency = self._wardrobe_tendency()
        if tendency:
            parts.append(f"整体服饰倾向：{tendency}")
        style = _single_line(selection.get("style"), 200)
        if style:
            parts.append(f"这一身的风格：{style}")
        outfit_name = _single_line(selection.get("outfit_name"), WARDROBE_MAX_NAME)
        if outfit_name:
            parts.append(f"整套：{outfit_name}")
        lines, truncated = self._wardrobe_detail_lines(
            picked, limit_items=WARDROBE_DETAIL_MAX_ITEMS, limit_chars=WARDROBE_DETAIL_MAX_CHARS
        )
        prompt_text = str(selection.get("prompt_text") or "").strip()
        if lines:
            parts.append("今天这身：\n" + "\n".join(lines))
        elif prompt_text:
            # 生成器路径：模型给的是整套描述，没有逐件 id 映射，直接原文给出。
            parts.append("今天这身（模型搭配）：\n" + prompt_text)
        else:
            parts.append("今天还没有裁决出具体的一套，可以参考整份清单再决定。")
        payload["count"] = len(picked)
        payload["source"] = str(selection.get("source") or "")
        payload["text"] = "\n".join(parts)
        payload["truncated"] = truncated
        return payload

    def _wardrobe_detail_reply(self, scope: Any = "today", slot: Any = "", user: Any = None) -> str:
        """工具的返回值：JSON 字符串（宿主会以 role:"tool" 回灌给模型）。"""

        try:
            payload = self._wardrobe_detail_payload(scope, slot, user)
        except Exception as exc:
            # 工具绝不能把异常抛回宿主的工具循环：宁可回一句「读不到」，
            # 也不要让整轮对话因为衣柜而失败。
            logger.warning("衣柜细节工具执行失败: %s", _single_line(exc, 160))
            return json.dumps({"status": "error", "text": "读取衣柜细节失败。"}, ensure_ascii=False)
        return json.dumps(payload, ensure_ascii=False)

    def _sync_wardrobe_detail_tool(self, req: Any) -> bool:
        """按请求挂载/摘下衣柜细节工具，返回「这次请求模型能不能调用它」。

        * 衣柜没启用（或注入关闭）→ 从这次请求的工具表里摘掉：模型不该看到一个
          查不出任何东西的工具；
        * 衣柜可用 → 确保它在工具表里。前提是**这次请求本来就开着工具通道**
          （req.func_tool 是有 tools 列表的 ToolSet）。为 None 说明本次没有启用
          function calling（例如模型不支持），这时不新建工具表去改变宿主行为。

        **只「摘」不「挂」**：宿主本来就会按工具自身的 active 状态、人格 tools 白名单
        与 tool_permissions 决定这次请求带哪些工具。我们若把自己取的原始对象塞回去，
        会 a) 复活管理员已经在后台停用的工具（get_func 在没有 active 同名工具时会
        退化返回 inactive 对象），b) 绕过 _PermissionGuardedTool 的权限代理，
        把「仅管理员」的工具对所有人开放。所以这里只回答「在不在」，不改变工具表。
        """

        tool_set = getattr(req, "func_tool", None)
        tools = getattr(tool_set, "tools", None)
        if not isinstance(tools, list):
            return False
        present = any(
            getattr(tool, "name", "") == WARDROBE_DETAIL_TOOL_NAME for tool in tools
        )
        if not self._wardrobe_detail_available():
            # 读写两个工具一起摘：只摘读工具会让写工具留下来，同一个开关下行为不一致。
            wardrobe_tools = {WARDROBE_DETAIL_TOOL_NAME, WARDROBE_INTENT_TOOL_NAME}
            if any(getattr(tool, "name", "") in wardrobe_tools for tool in tools):
                tools[:] = [
                    tool for tool in tools
                    if getattr(tool, "name", "") not in wardrobe_tools
                ]
            return False
        return present

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
                if parsed is None and not text:
                    # 完全空返回：多半是推理预算被思考过程吃光了，换提示词
                    # 比换 Provider 更有效（同一条链路上其它候选往往是同一个模型）。
                    try:
                        retry_call = provider.text_chat(
                            prompt=prompt + _WARDROBE_VISION_TERSE_SUFFIX,
                            image_urls=image_urls,
                        )
                        retry_result = await asyncio.wait_for(
                            retry_call, timeout=_WARDROBE_VISION_TIMEOUT_SECONDS
                        )
                        text = str(
                            getattr(retry_result, "completion_text", retry_result) or ""
                        ).strip()
                        parsed = parse_wardrobe_image_reply(text)
                    except asyncio.TimeoutError:
                        failure = "识图超时，请稍后再试或换一张图。"
                        logger.warning("衣柜识图重试超时: provider=%s", provider_id_candidate)
                    except Exception as exc:
                        logger.warning(
                            "衣柜识图重试失败: provider=%s error=%s",
                            provider_id_candidate,
                            _single_line(exc, 160),
                        )
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

    def _import_wardrobe_asset(
        self, path: str, *, origin: str = ASSET_ORIGIN_PANEL, note: str = ""
    ) -> str:
        """Best-effort copy of one image into the asset store.

        Returns "" when the plugin has no data dir or the copy fails：素材落盘失败
        不该让"加衣物"这条命令整体失败，衣物本身仍然能入库。
        """

        data_dir = getattr(self, "data_dir", "")
        if not data_dir:
            return ""
        try:
            record, _ = import_asset(data_dir, path, origin=origin, origin_note=note)
        except Exception as exc:
            logger.debug("衣柜素材导入失败: %s", _single_line(exc, 160))
            return ""
        return str(record.get("id") or "")

    # ------------------------------------------------------------------
    # 草稿队列（素材 → 语义的人工确认环节）
    #
    # 批量导入只落草稿、不碰衣柜：识图会错，落库必须有人点头。这三个方法
    # 与 scripts/wardrobe_review.py 共用同一套数据层（list_pending_drafts /
    # apply_wardrobe_draft / mark_asset_status），所以面板与 CLI 不会分叉。
    # ------------------------------------------------------------------

    @staticmethod
    def _wardrobe_draft_row(row: Any) -> dict[str, Any]:
        """把素材层的待办行整理成面板可以直接渲染的形状。"""

        payload = row if isinstance(row, Mapping) else {}
        kind = _single_line(payload.get("kind"), 32)
        slot = _single_line(payload.get("slot"), 20)
        origin = _single_line(payload.get("origin"), 32)
        suffix = Path(str(payload.get("path") or "")).suffix.casefold()
        tags: list[str] = []
        for tag in payload.get("tags") or ():
            text = _single_line(tag, WARDROBE_MAX_TAG)
            if text and text not in tags:
                tags.append(text)
        try:
            width = max(0, int(payload.get("width") or 0))
            height = max(0, int(payload.get("height") or 0))
        except (TypeError, ValueError):
            width = height = 0
        return {
            "asset_id": _single_line(payload.get("asset_id"), 80),
            "kind": kind,
            # 还没识图的素材 kind 是空的，别显示成"未知类型"吓人。
            "kind_label": WARDROBE_DRAFT_KIND_LABELS.get(kind, kind or "待识图"),
            "name": _single_line(payload.get("name"), WARDROBE_MAX_NAME),
            "description": _single_line(payload.get("description"), WARDROBE_MAX_DESCRIPTION),
            "slot": slot,
            "slot_label": WARDROBE_SLOT_LABELS.get(slot, "未分类"),
            "tags": tags,
            "origin": origin,
            "origin_label": WARDROBE_ASSET_ORIGIN_LABELS.get(origin, origin or "未知来源"),
            "has_draft": bool(payload.get("has_draft")),
            "has_image": suffix in _WARDROBE_DRAFT_IMAGE_SUFFIXES,
            "width": width,
            "height": height,
        }

    def _wardrobe_pending_drafts(self) -> list[dict[str, Any]]:
        """列出等待确认的草稿（素材状态仍是 imported）。

        读不到数据目录或索引损坏时返回空列表：面板只该看到"队列是空的"，
        而不是一条读不懂的报错。
        """

        data_dir = str(getattr(self, "data_dir", "") or "")
        if not data_dir:
            return []
        try:
            rows = list_pending_drafts(data_dir)
        except Exception as exc:
            logger.warning("读取衣柜草稿队列失败: %s", _single_line(exc, 160))
            return []
        return [self._wardrobe_draft_row(row) for row in rows or ()]

    def _wardrobe_draft_overrides(self, overrides: Any) -> dict[str, Any]:
        """只认面板能就地修改的字段；缺省或未提供的键一律不动。"""

        payload = overrides if isinstance(overrides, Mapping) else {}
        merged: dict[str, Any] = {}
        for key, limit in (
            ("name", WARDROBE_MAX_NAME),
            ("description", WARDROBE_MAX_DESCRIPTION),
            ("slot", WARDROBE_MAX_TAG),
        ):
            if key not in payload or payload.get(key) is None:
                continue
            merged[key] = _single_line(payload.get(key), limit)
        return merged

    async def _wardrobe_confirm_draft(
        self, asset_id: Any, overrides: Any = None
    ) -> dict[str, Any]:
        """确认一条草稿；可带名称/描述/部位的覆盖。

        顺序是「先落库、再推进素材状态」：保存失败时素材仍是 imported，
        用户刷新队列还能重试，而不是得到一条既没入库又不能重试的孤儿。
        """

        clean_id = _single_line(asset_id, 80)
        outcome: dict[str, Any] = {
            "asset_id": clean_id,
            "ok": False,
            "kind": "",
            "name": "",
            "replaced": False,
            "error": "",
        }
        if not clean_id:
            outcome["error"] = "缺少素材编号。"
            return outcome
        data_dir = str(getattr(self, "data_dir", "") or "")
        if not data_dir:
            outcome["error"] = "插件没有数据目录，草稿队列不可用。"
            return outcome
        try:
            record = load_asset_index(data_dir).get(clean_id)
        except Exception as exc:
            logger.warning("读取衣柜素材索引失败: %s", _single_line(exc, 160))
            record = None
        if record is None:
            outcome["error"] = "素材不在索引里，刷新队列看看。"
            return outcome
        if str(record.get("status") or ASSET_STATUS_IMPORTED) != ASSET_STATUS_IMPORTED:
            outcome["error"] = "这条素材已经处理过了，刷新队列看看。"
            return outcome
        try:
            draft = load_asset_draft(data_dir, clean_id)
        except Exception as exc:
            logger.warning("读取衣柜草稿失败: %s", _single_line(exc, 160))
            draft = None
        if not draft:
            outcome["error"] = "这条素材还没有草稿（先跑识图）。"
            return outcome
        merged = dict(draft)
        merged.update(self._wardrobe_draft_overrides(overrides))
        source = ""
        try:
            source = str(asset_abs_path(data_dir, record))
        except Exception:
            source = ""
        # 分流只有一处实现（数据层 apply_wardrobe_draft）：命令路径、CLI 与
        # 面板队列必须落在同一个库里，否则"整套"会时不时钻进散件列表。
        items, outfits, applied = apply_wardrobe_draft(
            self._wardrobe_items(), self._wardrobe_outfits(), merged, asset_id=clean_id, source=source
        )
        outcome["kind"] = str(applied.get("kind") or "")
        outcome["name"] = str(applied.get("name") or "")
        outcome["replaced"] = bool(applied.get("replaced"))
        if not applied.get("ok"):
            outcome["error"] = str(applied.get("error") or "没有识别出可用的衣物。")
            return outcome
        if not await self._save_wardrobe_state(items=items, outfits=outfits):
            outcome["error"] = "草稿已应用，但保存失败，请到面板确认配置是否可写。"
            return outcome
        try:
            mark_asset_status(data_dir, clean_id, ASSET_STATUS_UNDERSTOOD)
        except Exception as exc:
            # 衣柜已经落库了，这一步失败只影响队列显示（会继续显示待确认），
            # 不该让用户重做一遍，所以只记日志。
            logger.warning("推进衣柜素材状态失败: %s", _single_line(exc, 160))
        # 把落库后的那一行也带回去：面板要并进本地列表，否则"刚确认完再点保存"
        # 会拿确认前的隐藏字段把它覆盖掉。
        # 用**精确同名**取回落库那一行：宽松查找（id/序号/子串）会取回别人那一行，
        # 面板把它并进本地列表后就串行了。
        stored = (
            find_wardrobe_outfit_by_exact_name(outfits, outcome["name"])
            if outcome["kind"] in (WARDROBE_IMAGE_KIND_OUTFIT, WARDROBE_IMAGE_KIND_REFERENCE)
            else find_wardrobe_item_by_exact_name(items, outcome["name"])
        )
        if stored:
            outcome["row"] = dict(stored)
        outcome["ok"] = True
        outcome["items_total"] = len(items)
        outcome["outfits_total"] = len(outfits)
        return outcome

    async def _wardrobe_reject_draft(self, asset_id: Any) -> dict[str, Any]:
        """丢弃一条草稿：只把素材推进到 rejected，衣柜一个字节都不动。"""

        clean_id = _single_line(asset_id, 80)
        outcome: dict[str, Any] = {"asset_id": clean_id, "ok": False, "error": ""}
        if not clean_id:
            outcome["error"] = "缺少素材编号。"
            return outcome
        data_dir = str(getattr(self, "data_dir", "") or "")
        if not data_dir:
            outcome["error"] = "插件没有数据目录，草稿队列不可用。"
            return outcome
        try:
            record = mark_asset_status(data_dir, clean_id, ASSET_STATUS_REJECTED)
        except Exception as exc:
            logger.warning("丢弃衣柜草稿失败: %s", _single_line(exc, 160))
            record = None
        if record is None:
            outcome["error"] = "素材不在索引里，刷新队列看看。"
            return outcome
        outcome["ok"] = True
        return outcome


    def _wardrobe_overview_text(self) -> str:
        tendency = self._wardrobe_tendency()
        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        lines = [f"角色衣柜：{len(items)}/{WARDROBE_MAX_ITEMS} 件、{len(outfits)}/{WARDROBE_MAX_OUTFITS} 套"]
        lines.append(f"启用：{'是' if self._wardrobe_enabled() else '否'}；写入提示词：{'是' if self._wardrobe_prompt_mode() else '否'}")
        lines.append(f"整体服饰倾向：{tendency or '（未设置）'}")
        if items:
            lines.append("具体衣物：")
            lines.extend(wardrobe_summary_lines(items, description_limit=60))
        else:
            lines.append("具体衣物：（还没有）")
        if outfits:
            lines.append("整套：")
            for index, outfit in enumerate(outfits, start=1):
                tag = "参考" if str(outfit.get("ownership") or "") == OWNERSHIP_REFERENCE else "自有"
                lines.append(f"{index}. {outfit['name']}（{tag}·{len(outfit.get('items') or [])} 件）")
        lines.append("")
        lines.append("维护方式：")
        lines.extend(self._wardrobe_help_lines())
        return "\n".join(lines)

    @staticmethod
    def _wardrobe_help_lines() -> list[str]:
        return [
            "陪伴 衣柜 倾向 <整体服饰倾向描述>",
            "陪伴 衣柜 添加 <名称> | <描述>",
            "陪伴 衣柜 添加图片 <可选备注>（带图或回复图片发送；自动区分散件与整套）",
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
        outfits = self._wardrobe_outfits()
        added: list[str] = []
        replaced: list[str] = []
        outfits_added: list[str] = []
        outfits_replaced: list[str] = []
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
            # 图片同时进素材层：图与语义记录解耦，删记录不删图。
            asset_id = self._import_wardrobe_asset(path, origin=ASSET_ORIGIN_PANEL, note=note)
            # 分流只有一处实现（数据层 apply_wardrobe_draft），命令路径与草稿队列共用。
            items, outfits, outcome = apply_wardrobe_draft(
                items, outfits, parsed, asset_id=asset_id, source=path
            )
            if not outcome.get("ok"):
                failures.append(str(outcome.get("error") or "没有识别出可用的衣物。"))
                if outcome.get("limit"):
                    break
                continue
            stored_name = str(outcome.get("name") or "")
            if str(outcome.get("kind") or "") in (
                WARDROBE_IMAGE_KIND_OUTFIT,
                WARDROBE_IMAGE_KIND_REFERENCE,
            ):
                (outfits_replaced if outcome.get("replaced") else outfits_added).append(stored_name)
            else:
                (replaced if outcome.get("replaced") else added).append(stored_name)
        if not (added or replaced or outfits_added or outfits_replaced):
            detail = chr(10).join(failures[:5]) if failures else "没有识别出可用的衣物。"
            return f"没有把衣物加入衣柜：{chr(10)}{detail}", ""
        if not await self._save_wardrobe_state(items=items, outfits=outfits):
            return "衣物已识别，但保存失败，请到面板确认配置是否可写。", ""
        lines = []
        if added:
            lines.append("已加入衣物：" + "、".join(added))
        if replaced:
            lines.append("已更新衣物：" + "、".join(replaced))
        if outfits_added:
            lines.append("已加入整套：" + "、".join(outfits_added))
        if outfits_replaced:
            lines.append("已更新整套：" + "、".join(outfits_replaced))
        if failures:
            lines.append("未处理：" + "；".join(failures[:3]))
        lines.append(
            f"衣柜现有 {len(items)}/{WARDROBE_MAX_ITEMS} 件、{len(outfits)}/{WARDROBE_MAX_OUTFITS} 套。"
        )
        return chr(10).join(lines), ""

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
