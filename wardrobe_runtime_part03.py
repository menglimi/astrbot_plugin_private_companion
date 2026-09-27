# -*- coding: utf-8 -*-
"""WardrobePart03Mixin。

由 tools/split_mixin_domain.py 从 wardrobe_runtime.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 567 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WardrobeMixin）。
"""
from __future__ import annotations

from .wardrobe_runtime_shared import (
    WARDROBE_ASSET_ORIGIN_LABELS,
    WARDROBE_DETAIL_MAX_CHARS,
    WARDROBE_DETAIL_MAX_ITEMS,
    WARDROBE_DETAIL_SCOPES,
    WARDROBE_DETAIL_TOOL_NAME,
    WARDROBE_DRAFT_KIND_LABELS,
    WARDROBE_INTENT_TOOL_NAME,
    _WARDROBE_DRAFT_IMAGE_SUFFIXES,
    _WARDROBE_VISION_TERSE_SUFFIX,
    _WARDROBE_VISION_TIMEOUT_SECONDS,
)
from .wardrobe_runtime_shared import ASSET_ORIGIN_PANEL
from .wardrobe_runtime_shared import ASSET_STATUS_IMPORTED
from .wardrobe_runtime_shared import ASSET_STATUS_REJECTED
from .wardrobe_runtime_shared import ASSET_STATUS_UNDERSTOOD
from .wardrobe_runtime_shared import Any
from .wardrobe_runtime_shared import Mapping
from .wardrobe_runtime_shared import Path
from .wardrobe_runtime_shared import WARDROBE_IMAGE_KIND_OUTFIT
from .wardrobe_runtime_shared import WARDROBE_IMAGE_KIND_REFERENCE
from .wardrobe_runtime_shared import WARDROBE_MAX_DESCRIPTION
from .wardrobe_runtime_shared import WARDROBE_MAX_NAME
from .wardrobe_runtime_shared import WARDROBE_MAX_TAG
from .wardrobe_runtime_shared import WARDROBE_SLOT_LABELS
from .wardrobe_runtime_shared import _single_line
from .wardrobe_runtime_shared import apply_wardrobe_draft
from .wardrobe_runtime_shared import asset_abs_path
from .wardrobe_runtime_shared import asyncio
from .wardrobe_runtime_shared import build_wardrobe_image_instruction
from .wardrobe_runtime_shared import find_wardrobe_item_by_exact_name
from .wardrobe_runtime_shared import find_wardrobe_outfit_by_exact_name
from .wardrobe_runtime_shared import import_asset
from .wardrobe_runtime_shared import json
from .wardrobe_runtime_shared import list_pending_drafts
from .wardrobe_runtime_shared import load_asset_draft
from .wardrobe_runtime_shared import load_asset_index
from .wardrobe_runtime_shared import logger
from .wardrobe_runtime_shared import mark_asset_status
from .wardrobe_runtime_shared import normalize_wardrobe_slot
from .wardrobe_runtime_shared import parse_wardrobe_image_reply



class WardrobePart03Mixin:
    """WardrobePart03Mixin（从 WardrobeMixin 拆出）。"""


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
