# -*- coding: utf-8 -*-
"""躯体健康域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 447 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiFoodBodyMixin:
    """躯体健康域（从 PrivateCompanionPageApi 拆出）。"""


    @staticmethod
    def _food_menu_parse_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "启用", "开启", "是", "常吃"}

    def _food_menu_parse_terms(self, value: Any, *, limit: int = 12, item_limit: int = 24) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[,，、\n/|#]+", str(value or ""))
        items: list[str] = []
        for raw in raw_items:
            item = self._single_line(raw, item_limit)
            if item and item not in items:
                items.append(item)
        return items[:limit]

    def _food_menu_normalize_times(self, value: Any) -> list[str]:
        mapping = {
            "早餐": "breakfast",
            "早饭": "breakfast",
            "早上": "breakfast",
            "breakfast": "breakfast",
            "午餐": "lunch",
            "午饭": "lunch",
            "中午": "lunch",
            "lunch": "lunch",
            "晚餐": "dinner",
            "晚饭": "dinner",
            "晚上": "dinner",
            "dinner": "dinner",
            "夜宵": "late_night",
            "宵夜": "late_night",
            "深夜": "late_night",
            "late_night": "late_night",
            "latenight": "late_night",
            "加餐": "snack",
            "零食": "snack",
            "下午茶": "snack",
            "snack": "snack",
        }
        normalized: list[str] = []
        for raw in self._food_menu_parse_terms(value, limit=5, item_limit=16):
            key = mapping.get(str(raw).strip().lower()) or mapping.get(str(raw).strip())
            if key and key not in normalized:
                normalized.append(key)
        return normalized

    def _food_menu_infer_fields(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        text = " ".join(
            str(payload.get(key) or "")
            for key in ("type", "category", "tags", "times", "note")
        )
        text = f"{name} {text}"
        item_type = self._single_line(payload.get("type"), 20)
        if not item_type:
            if any(token in text for token in ("奶茶", "咖啡", "甜品", "蛋糕", "水果", "零食", "饮料", "酸奶")):
                item_type = "drink_snack"
            elif any(token in text for token in ("外卖", "美团", "饿了么", "配送", "到家")):
                item_type = "takeout"
            elif any(token in text for token in ("店", "馆", "楼下", "附近", "食堂", "餐厅", "小吃街")):
                item_type = "restaurant"
            elif any(token in text for token in ("泡面", "速食", "面包", "麦片", "饼干", "应急")):
                item_type = "emergency"
            else:
                item_type = "dish"
        allowed_types = {"dish", "restaurant", "takeout", "drink_snack", "emergency"}
        if item_type not in allowed_types:
            item_type = "dish"

        category = self._single_line(payload.get("category"), 24)
        if not category:
            category_rules = [
                ("面食", ("面", "粉", "馄饨", "饺子", "抄手", "米线", "米粉", "螺蛳粉")),
                ("米饭", ("饭", "盖浇", "便当", "煲仔", "黄焖鸡", "咖喱")),
                ("快餐", ("汉堡", "炸鸡", "披萨", "麦当劳", "肯德基", "华莱士", "塔斯汀")),
                ("甜口", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
                ("热锅", ("火锅", "麻辣烫", "冒菜", "砂锅", "关东煮")),
                ("应急", ("泡面", "速食", "面包", "麦片", "饼干")),
            ]
            category = next((label for label, tokens in category_rules if any(token in text for token in tokens)), "")

        tags = self._food_menu_parse_terms(payload.get("tags"), limit=10, item_limit=16)
        inferred_tags: list[str] = []
        tag_rules = [
            ("热乎", ("面", "粉", "粥", "汤", "馄饨", "米线", "火锅", "麻辣烫", "砂锅", "关东煮")),
            ("快", ("泡面", "速食", "便当", "外卖", "汉堡", "炸鸡", "麦当劳", "肯德基", "华莱士", "塔斯汀")),
            ("清淡", ("粥", "汤", "沙拉", "蒸", "清淡")),
            ("辣", ("辣", "麻辣", "川", "火锅", "冒菜", "麻辣烫", "螺蛳粉")),
            ("甜", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("顶饱", ("饭", "面", "粉", "汉堡", "便当", "盖浇", "黄焖鸡")),
            ("便宜", ("食堂", "快餐", "兰州", "沙县", "华莱士")),
        ]
        for tag, tokens in tag_rules:
            if any(token in text for token in tokens) and tag not in tags and tag not in inferred_tags:
                inferred_tags.append(tag)
        tags = (tags + inferred_tags)[:10]

        times = self._food_menu_normalize_times(payload.get("times"))
        if not times:
            if any(token in text for token in ("早餐", "早饭", "早上", "包子", "豆浆", "油条", "麦片", "面包")):
                times.append("breakfast")
            if any(token in text for token in ("奶茶", "咖啡", "甜品", "水果", "零食", "酸奶")):
                times.append("snack")
            if any(token in text for token in ("夜宵", "宵夜", "烧烤", "炸鸡", "泡面", "螺蛳粉")):
                times.append("late_night")
        return {"type": item_type, "category": category, "tags": tags, "times": times}

    def _food_menu_payload_from_line(self, line: str) -> dict[str, Any]:
        text = self._single_line(line, 220)
        parts = [self._single_line(part, 80) for part in re.split(r"[|｜\t]+", text) if self._single_line(part, 80)]
        name = self._single_line(parts[0] if parts else text, 40)
        payload: dict[str, Any] = {"name": name}
        if len(parts) >= 2:
            payload["category"] = parts[1]
        if len(parts) >= 3:
            payload["tags"] = parts[2]
        if len(parts) >= 4:
            payload["times"] = parts[3]
        return payload

    async def update_food_menu(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        item_id = self._single_line(payload.get("id"), 48)
        name = self._single_line(payload.get("name"), 40)

        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("food_menu", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["food_menu"] = state
                items = state.setdefault("items", [])
                if not isinstance(items, list):
                    items = []
                    state["items"] = items

                if payload.get("delete"):
                    before = len(items)
                    state["items"] = [item for item in items if not (isinstance(item, dict) and self._single_line(item.get("id"), 48) == item_id)]
                    state["updated_ts"] = time.time()
                    self.plugin._save_data_sync(sections={"food_menu"})
                    return self._ok({
                        "changed": len(state["items"]) != before,
                        "message": "已移出候选",
                        "food_menu": self._food_menu_summary(self.plugin.data),
                    })

                if not name:
                    return self._error("缺少名称")
                if not item_id:
                    for existing in items:
                        if not isinstance(existing, dict):
                            continue
                        existing_name = self._single_line(existing.get("name"), 40)
                        aliases = self._food_menu_parse_terms(existing.get("aliases"), limit=12, item_limit=24)
                        if name == existing_name or name in aliases:
                            item_id = self._single_line(existing.get("id"), 48)
                            break
                if not item_id:
                    item_id = f"food_{hashlib.sha1((name + str(time.time())).encode('utf-8')).hexdigest()[:12]}"

                existing_index = -1
                existing_item: dict[str, Any] = {}
                for index, item in enumerate(items):
                    if isinstance(item, dict) and self._single_line(item.get("id"), 48) == item_id:
                        existing_index = index
                        existing_item = dict(item)
                        break

                inferred = self._food_menu_infer_fields(name, payload)
                if "type" in payload:
                    item_type = self._single_line(payload.get("type"), 20) or inferred.get("type") or "dish"
                else:
                    item_type = self._single_line(existing_item.get("type"), 20) or inferred.get("type") or "dish"
                allowed_types = {"dish", "restaurant", "takeout", "drink_snack", "emergency"}
                if item_type not in allowed_types:
                    item_type = "dish"
                if "category" in payload:
                    category = self._single_line(payload.get("category"), 24)
                    if not category and existing_index < 0:
                        category = self._single_line(inferred.get("category"), 24)
                else:
                    category = self._single_line(existing_item.get("category"), 24) or self._single_line(inferred.get("category"), 24)
                if "tags" in payload:
                    tags = self._food_menu_parse_terms(payload.get("tags"), limit=10, item_limit=16)
                else:
                    tags = self._food_menu_parse_terms(existing_item.get("tags"), limit=10, item_limit=16)
                if not tags and existing_index < 0:
                    tags = list(inferred.get("tags") or [])
                if "times" in payload:
                    times = self._food_menu_normalize_times(payload.get("times"))
                else:
                    times = self._food_menu_normalize_times(existing_item.get("times"))
                if not times and existing_index < 0:
                    times = list(inferred.get("times") or [])
                item = dict(existing_item)
                item.update(
                    {
                        "id": item_id,
                        "name": name,
                        "type": item_type,
                        "category": category,
                        "tags": tags[:10],
                        "times": times[:5],
                        "avoid": self._food_menu_parse_terms(payload.get("avoid"), limit=8, item_limit=24),
                        "aliases": [value for value in self._food_menu_parse_terms(payload.get("aliases"), limit=10, item_limit=24) if value != name],
                        "note": self._single_line(payload.get("note"), 100),
                        "favorite": self._food_menu_parse_bool(payload.get("favorite"), bool(existing_item.get("favorite"))),
                        "hidden": self._food_menu_parse_bool(payload.get("hidden"), bool(existing_item.get("hidden"))),
                        "use_count": self._int(payload.get("use_count")) if "use_count" in payload else self._int(existing_item.get("use_count")),
                        "created_ts": self._float(existing_item.get("created_ts")) or time.time(),
                        "updated_ts": time.time(),
                        "last_used_at": self._float(existing_item.get("last_used_at")),
                        "last_recommended_at": self._float(existing_item.get("last_recommended_at")),
                    }
                )
                if existing_index >= 0:
                    items[existing_index] = item
                else:
                    items.append(item)
                state["updated_ts"] = time.time()
                self.plugin._save_data_sync(sections={"food_menu"})
                return self._ok({"message": "已保存候选", "food_menu": self._food_menu_summary(self.plugin.data)})
        except Exception as exc:
            logger.error(f"更新吃什么候选失败: {exc}", exc_info=True)
            return self._exception_error("更新吃什么候选失败")

    async def bulk_update_food_menu(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_text = str(payload.get("text") or payload.get("items") or "").strip()
        favorite = self._food_menu_parse_bool(payload.get("favorite"), False)
        hidden = self._food_menu_parse_bool(payload.get("hidden"), False)
        if not raw_text:
            return self._error("先粘贴几样候选")
        lines = [
            self._single_line(part, 220)
            for part in re.split(r"[\r\n;；]+", raw_text)
            if self._single_line(part, 220)
        ]
        if not lines:
            return self._error("没有读到候选")
        added = 0
        updated = 0
        skipped = 0
        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("food_menu", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["food_menu"] = state
                items = state.setdefault("items", [])
                if not isinstance(items, list):
                    items = []
                    state["items"] = items
                for line in lines[:80]:
                    item_payload = self._food_menu_payload_from_line(line)
                    name = self._single_line(item_payload.get("name"), 40)
                    if not name:
                        skipped += 1
                        continue
                    existing_index = -1
                    existing_item: dict[str, Any] = {}
                    for index, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        existing_name = self._single_line(item.get("name"), 40)
                        aliases = self._food_menu_parse_terms(item.get("aliases"), limit=12, item_limit=24)
                        if name == existing_name or name in aliases:
                            existing_index = index
                            existing_item = dict(item)
                            break
                    inferred = self._food_menu_infer_fields(name, item_payload)
                    item_id = self._single_line(existing_item.get("id"), 48)
                    if not item_id:
                        item_id = f"food_{hashlib.sha1((name + str(time.time())).encode('utf-8')).hexdigest()[:12]}"
                    now = time.time()
                    existing_tags = self._food_menu_parse_terms(existing_item.get("tags"), limit=10, item_limit=16)
                    inferred_tags = [tag for tag in (inferred.get("tags") or []) if tag not in existing_tags]
                    existing_times = self._food_menu_normalize_times(existing_item.get("times"))
                    inferred_times = [time_key for time_key in (inferred.get("times") or []) if time_key not in existing_times]
                    item = dict(existing_item)
                    item.update(
                        {
                            "id": item_id,
                            "name": name,
                            "type": self._single_line(existing_item.get("type"), 20) or inferred.get("type") or "dish",
                            "category": self._single_line(item_payload.get("category"), 24)
                            or self._single_line(existing_item.get("category"), 24)
                            or self._single_line(inferred.get("category"), 24),
                            "tags": (existing_tags + inferred_tags)[:10],
                            "times": (existing_times + inferred_times)[:5],
                            "note": self._single_line(existing_item.get("note"), 100),
                            "favorite": bool(existing_item.get("favorite")) or favorite,
                            "hidden": bool(existing_item.get("hidden")) or hidden,
                            "use_count": self._int(existing_item.get("use_count")),
                            "created_ts": self._float(existing_item.get("created_ts")) or now,
                            "updated_ts": now,
                            "last_used_at": self._float(existing_item.get("last_used_at")),
                            "last_recommended_at": self._float(existing_item.get("last_recommended_at")),
                        }
                    )
                    if existing_index >= 0:
                        items[existing_index] = item
                        updated += 1
                    else:
                        items.append(item)
                        added += 1
                state["updated_ts"] = time.time()
                self.plugin._save_data_sync(sections={"food_menu"})
                return self._ok(
                    {
                        "message": f"已加入 {added} 个，更新 {updated} 个" + (f"，跳过 {skipped} 个" if skipped else ""),
                        "added": added,
                        "updated": updated,
                        "skipped": skipped,
                        "food_menu": self._food_menu_summary(self.plugin.data),
                    }
                )
        except Exception as exc:
            logger.error(f"批量更新吃什么候选失败: {exc}", exc_info=True)
            return self._exception_error("批量更新吃什么候选失败")

    async def bulk_delete_food_menu(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        item_ids: list[str] = []
        for raw_id in raw_ids[:160]:
            item_id = self._single_line(raw_id, 48)
            if item_id and item_id not in item_ids:
                item_ids.append(item_id)
        if not item_ids:
            return self._error("请先选择要删除的候选")
        selected = set(item_ids)
        try:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("food_menu", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["food_menu"] = state
                items = state.get("items") if isinstance(state.get("items"), list) else []
                existing_ids = {
                    self._single_line(item.get("id"), 48)
                    for item in items
                    if isinstance(item, dict) and self._single_line(item.get("id"), 48)
                }
                remaining = [
                    item
                    for item in items
                    if not (isinstance(item, dict) and self._single_line(item.get("id"), 48) in selected)
                ]
                deleted_ids = [item_id for item_id in item_ids if item_id in existing_ids]
                missing_ids = [item_id for item_id in item_ids if item_id not in existing_ids]
                if deleted_ids:
                    state["items"] = remaining
                    state["updated_ts"] = time.time()
                    self.plugin._save_data_sync(sections={"food_menu"})
                return self._ok(
                    {
                        "message": f"已删除 {len(deleted_ids)} 个候选",
                        "deleted": len(deleted_ids),
                        "deleted_ids": deleted_ids,
                        "missing_ids": missing_ids,
                        "food_menu": self._food_menu_summary(self.plugin.data),
                    }
                )
        except Exception as exc:
            logger.error(f"批量删除吃什么候选失败: {exc}", exc_info=True)
            return self._exception_error("批量删除吃什么候选失败")

    @staticmethod
    def _body_monitor_status_state(raw: dict[str, Any], *, enabled: bool, installed: bool) -> str:
        if not enabled:
            return "disabled"
        aliases = {
            "missing": "not_installed",
            "unavailable": "not_installed",
            "not-installed": "not_installed",
            "version_mismatch": "incompatible",
            "version-mismatch": "incompatible",
            "unsupported": "incompatible",
            "waiting": "initializing",
            "starting": "initializing",
            "ready": "connected",
            "active": "connected",
            "ok": "connected",
            "failed": "error",
        }
        state = str(raw.get("state") or raw.get("status") or "").strip().lower()
        state = aliases.get(state, state)
        if state == "disabled":
            state = "initializing"
        if state in {"disabled", "not_installed", "incompatible", "initializing", "connected", "error"}:
            return state
        if not installed or raw.get("available") is False:
            return "not_installed"
        if raw.get("compatible") is False or raw.get("api_compatible") is False:
            return "incompatible"
        if raw.get("connected") is True:
            return "connected"
        if raw.get("last_error") or raw.get("error"):
            return "error"
        return "initializing"

    def _body_monitor_status_error(self, value: Any) -> str:
        return "Body Monitor 事件读取失败，请查看服务端日志" if self._single_line(value, 240) else ""

    async def _sync_body_monitor_integration_toggle(self, enabled: bool) -> None:
        integration = getattr(self.plugin, "_body_monitor_integration", None)
        setter = getattr(integration, "set_enabled", None)
        if callable(setter):
            try:
                result = setter(enabled)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.warning(
                    "Body Monitor 联动运行态切换失败: enabled=%s error=%s",
                    enabled,
                    self._single_line(exc, 160),
                )
                return
        if enabled:
            kicker = getattr(self.plugin, "_kick_proactive_loop_once", None)
            if callable(kicker):
                try:
                    result = kicker()
                    if hasattr(result, "__await__"):
                        task = asyncio.create_task(
                            result,
                            name="private_companion_body_monitor_kick",
                        )
                        self.plugin._body_monitor_integration_kick_task = task

                        def _consume_kick_result(finished: asyncio.Task[Any]) -> None:
                            try:
                                finished.result()
                            except asyncio.CancelledError:
                                pass
                            except Exception as exc:
                                logger.warning(
                                    "Body Monitor 联动即时拉取失败: %s",
                                    self._single_line(exc, 160),
                                )

                        task.add_done_callback(_consume_kick_result)
                except Exception as exc:
                    logger.warning(
                        "Body Monitor 联动即时拉取触发失败: %s",
                        self._single_line(exc, 160),
                    )
