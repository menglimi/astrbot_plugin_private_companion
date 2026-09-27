# -*- coding: utf-8 -*-
"""WardrobePart04Mixin。

由 tools/split_mixin_domain.py 从 wardrobe_runtime.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 221 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WardrobeMixin）。
"""
from __future__ import annotations
from .wardrobe_runtime_shared import ASSET_ORIGIN_PANEL
from .wardrobe_runtime_shared import AstrMessageEvent
from .wardrobe_runtime_shared import OWNERSHIP_REFERENCE
from .wardrobe_runtime_shared import SOURCE_KIND_MANUAL
from .wardrobe_runtime_shared import WARDROBE_IMAGE_KIND_OUTFIT
from .wardrobe_runtime_shared import WARDROBE_IMAGE_KIND_REFERENCE
from .wardrobe_runtime_shared import WARDROBE_MAX_ITEMS
from .wardrobe_runtime_shared import WARDROBE_MAX_OUTFITS
from .wardrobe_runtime_shared import WardrobeError
from .wardrobe_runtime_shared import WardrobeLimitError
from .wardrobe_runtime_shared import _single_line
from .wardrobe_runtime_shared import add_wardrobe_item
from .wardrobe_runtime_shared import apply_wardrobe_draft
from .wardrobe_runtime_shared import clear_wardrobe
from .wardrobe_runtime_shared import delete_wardrobe_item
from .wardrobe_runtime_shared import normalize_wardrobe_tendency
from .wardrobe_runtime_shared import update_wardrobe_item
from .wardrobe_runtime_shared import wardrobe_summary_lines



class WardrobePart04Mixin:
    """WardrobePart04Mixin（从 WardrobeMixin 拆出）。"""


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
