# -*- coding: utf-8 -*-
"""WardrobePart01Mixin。

由 tools/split_mixin_domain.py 从 wardrobe_runtime.py 机械抽取（33 个方法 + 0 个模块级名字 + 0 个类级赋值 / 546 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WardrobeMixin）。
"""
from __future__ import annotations

from .wardrobe_runtime_shared import (
    WARDROBE_DETAIL_FULL,
    WARDROBE_DETAIL_PROGRESSIVE,
    WARDROBE_INTENT_KEY,
    WARDROBE_MINIMAL_MAX_CHARS,
    WARDROBE_OVERRIDE_MAX_ITEMS,
    _MISSING,
    _WARDROBE_DETAIL_CONTEXT_TRIGGERS,
    _WARDROBE_DETAIL_CONTEXT_WORDS,
    _WARDROBE_DETAIL_TRIGGERS,
    _WARDROBE_DETAIL_TRIGGER_PATTERNS,
    _WARDROBE_VISION_MAX_IMAGES,
)
from .wardrobe_runtime_shared import Any
from .wardrobe_runtime_shared import Mapping
from .wardrobe_runtime_shared import OWNERSHIP_OWNED
from .wardrobe_runtime_shared import OWNERSHIP_REFERENCE
from .wardrobe_runtime_shared import PERSONA_SETTINGS_KEY
from .wardrobe_runtime_shared import WARDROBE_MAX_ITEMS
from .wardrobe_runtime_shared import WARDROBE_PROMPT_MAX_CHARS
from .wardrobe_runtime_shared import WARDROBE_PROMPT_MAX_ITEMS
from .wardrobe_runtime_shared import WARDROBE_PROMPT_PREAMBLE
from .wardrobe_runtime_shared import _flat_get
from .wardrobe_runtime_shared import _set_into_config
from .wardrobe_runtime_shared import _single_line
from .wardrobe_runtime_shared import _today_key
from .wardrobe_runtime_shared import deepcopy
from .wardrobe_runtime_shared import logger
from .wardrobe_runtime_shared import normalize_wardrobe_image_prompt
from .wardrobe_runtime_shared import normalize_wardrobe_items
from .wardrobe_runtime_shared import normalize_wardrobe_outfits
from .wardrobe_runtime_shared import normalize_wardrobe_tendency
from .wardrobe_runtime_shared import render_reference_profile
from .wardrobe_runtime_shared import render_worn_items
from .wardrobe_runtime_shared import runtime_persona_setting
from .wardrobe_runtime_shared import select_wardrobe_outfit
from .wardrobe_runtime_shared import truncate_wardrobe_text



class WardrobePart01Mixin:
    """WardrobePart01Mixin（从 WardrobeMixin 拆出）。"""


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
