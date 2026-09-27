# -*- coding: utf-8 -*-
"""chain_test 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 214 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiChainTestMixin:
    """chain_test 域（从 PrivateCompanionPageApi 拆出）。"""


    async def _run_screen_peek_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        screen_plugin = getter() if callable(getter) else None
        if screen_plugin is None or not callable(getattr(screen_plugin, "_invoke_screen_skill", None)):
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "error": "未检测到可用的 screen_companion 插件或识屏入口",
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._single_line(payload.get("umo"), 180) or self._preferred_tts_test_umo(users)
        event = None
        if umo and callable(getattr(screen_plugin, "_create_virtual_event", None)):
            try:
                event = screen_plugin._create_virtual_event(umo)
            except Exception as exc:
                logger.info(
                    "窥屏排障虚拟事件创建失败,将无事件调用: umo=%s error=%s",
                    self._single_line(umo, 120),
                    self._single_line(exc, 120),
                )
        prompt = self._single_line(payload.get("prompt"), 500) or (
            "这是一次插件排障中心发起的授权识屏链路测试。请只判断当前屏幕观察能力是否可用，"
            "用一句很短的内部摘要描述大概画面类型；不要输出账号、完整聊天内容、隐私细节或长文本。"
        )
        started = time.time()
        try:
            raw_result = await asyncio.wait_for(
                screen_plugin._invoke_screen_skill(
                    event,
                    request_prompt=prompt,
                    history_user_text="Private Companion 排障中心正在测试 screen_companion 识屏链路。",
                    task_id="private_companion_troubleshooting_screen_peek",
                ),
                timeout=max(15, self._int(payload.get("timeout_seconds"), 60, 5, 180)),
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            error = self._single_line(exc, 220)
            logger.warning("窥屏排障测试失败: %s", error, exc_info=True)
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "umo": umo,
                "elapsed_ms": elapsed_ms,
                "error": error or repr(exc),
            }
        elapsed_ms = int((time.time() - started) * 1000)
        context = "screen_peek：\n" + (self._single_line(raw_result, 500) if raw_result else "没有得到屏幕观察结果")
        unusable_checker = getattr(self.plugin, "_is_unusable_screen_peek_context", None)
        unusable = bool(unusable_checker(context)) if callable(unusable_checker) else not bool(raw_result)
        preview = self._single_line(raw_result, 220)
        logger.info(
            "窥屏排障测试结束: ok=%s elapsed=%sms umo=%s preview=%s",
            not unusable,
            elapsed_ms,
            self._single_line(umo, 120),
            preview,
        )
        return {
            "ok": not unusable,
            "title": "窥屏链路测试",
            "umo": umo,
            "provider": "screen_companion",
            "detail": "已成功获得屏幕观察摘要" if not unusable else "识屏返回为空或不可用结果",
            "text_preview": preview,
            "context_chars": len(str(raw_result or "")),
            "elapsed_ms": elapsed_ms,
            "error": "" if not unusable else (preview or "没有得到屏幕观察结果"),
        }

    async def _run_qzone_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str) -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16),
                    "detail": self._single_line(detail, 180),
                }
            )

        started = time.time()
        service_available = bool(
            callable(getattr(self.plugin, "_test_qzone_integration", None))
            and callable(getattr(self.plugin, "_qzone_get_cookies", None))
        )
        platform_checker = getattr(self.plugin, "_qzone_platform_supported", None)
        platform_supported = bool(platform_checker(None)) if callable(platform_checker) else True
        enabled = bool(getattr(self.plugin, "enable_qzone_integration", False) and platform_supported)
        comment_enabled = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        add_step("内置服务", "ok" if service_available else "error", "可用" if service_available else "QQ 空间模块入口不可用")
        add_step(
            "平台能力",
            "ok" if platform_supported else "error",
            "OneBot/aiocqhttp 可用" if platform_supported else "QQ 官方机器人不支持 QQ 空间",
        )
        add_step("整合开关", "ok" if enabled else "warn", "已开启" if enabled else "已关闭")
        if not service_available or not enabled:
            unavailable_detail = (
                "QQ 官方机器人不支持 QQ 空间；不会执行读取、发布、点赞、评论或后台轮询。"
                if not platform_supported
                else "QQ 空间整合未启用或模块入口不可用"
            )
            return {
                "ok": False,
                "title": "QQ 空间链路测试",
                "provider": "qzone",
                "detail": unavailable_detail,
                "text_preview": unavailable_detail if not platform_supported else "开启 QQ 空间整合后再测试 Cookie、读取和发布工具链路。",
                "steps": steps,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": unavailable_detail,
            }

        target_id = self._single_line(payload.get("target_id"), 40)
        read_text = ""
        read_ok = False
        read_detail = ""
        reader = getattr(self.plugin, "_test_qzone_integration", None)
        if callable(reader):
            try:
                read_text = await asyncio.wait_for(
                    reader(None, target_id=target_id),
                    timeout=max(15, self._int(payload.get("timeout_seconds"), 45, 10, 180)),
                )
                read_ok = ("读取链路正常" in read_text) or ("读取链路可调用" in read_text)
                read_detail = (
                    (self._qzone_test_line_with_prefix(read_text, "查询结果：失败") if not read_ok else "")
                    or self._qzone_test_last_result_line(read_text)
                    or self._single_line(read_text, 180)
                )
                add_step("Cookie/读取", "ok" if read_ok else "error", read_detail or "读取测试未返回明确结果")
            except Exception as exc:
                read_detail = self._single_line(exc, 180)
                add_step("Cookie/读取", "error", read_detail or "读取测试异常")
        else:
            add_step("Cookie/读取", "error", "缺少 _test_qzone_integration 测试入口")

        publish_ok = False
        publish_detail = ""
        publisher = getattr(self.plugin, "_pc_qzone_publish_feed_impl", None)
        if callable(publisher):
            try:
                raw = await asyncio.wait_for(publisher(None, ""), timeout=15)
                parsed = json.loads(raw) if isinstance(raw, str) else {}
                status = self._single_line(parsed.get("status"), 40) if isinstance(parsed, dict) else ""
                message = self._single_line(parsed.get("message"), 160) if isinstance(parsed, dict) else self._single_line(raw, 160)
                publish_ok = status == "need_text"
                publish_detail = "空参数返回 need_text，发布工具入口正常" if publish_ok else (message or f"返回 {status or '未知状态'}")
                add_step("发布模拟", "ok" if publish_ok else "warn", publish_detail)
            except Exception as exc:
                publish_detail = self._single_line(exc, 180)
                add_step("发布模拟", "error", publish_detail or "发布工具空参数测试异常")
        else:
            add_step("发布模拟", "warn", "缺少发布工具入口，无法测试空参数模拟")

        async with self.plugin._data_lock:
            qzone_state = deepcopy(
                self.plugin.data.get("qzone_integration")
                if isinstance(self.plugin.data.get("qzone_integration"), dict)
                else {}
            )
        list_len = lambda key: len(qzone_state.get(key)) if isinstance(qzone_state.get(key), list) else 0
        seen_count = list_len("comment_inbox_seen_ids") + list_len("comment_inbox_seen_keys")
        replied_count = list_len("comment_inbox_replied_ids") + list_len("comment_inbox_replied_keys")
        inbox_status = self._single_line(qzone_state.get("last_comment_inbox_status"), 120)
        inbox_detail = (
            f"{'已开启' if comment_enabled else '未开启'}；已见 {seen_count}，已回复 {replied_count}"
            + (f"；最近 {inbox_status}" if inbox_status else "")
        )
        add_step("评论收件箱", "ok" if comment_enabled else "info", inbox_detail)

        ok = bool(read_ok and publish_ok)
        preview_parts = [
            read_detail,
            publish_detail,
            inbox_detail,
        ]
        if read_text:
            preview_parts.append(self._single_line(read_text, 500))
        return {
            "ok": ok,
            "title": "QQ 空间链路测试",
            "provider": "qzone",
            "detail": "QQ 空间读取和发布模拟正常" if ok else "QQ 空间链路存在需要处理的项",
            "text_preview": self._single_line("；".join(part for part in preview_parts if part), 500),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "" if ok else (read_detail or publish_detail or "QQ 空间链路测试未通过"),
        }

    @staticmethod
    def _qzone_test_last_result_line(text: str) -> str:
        for line in reversed(str(text or "").replace("\r", "\n").split("\n")):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith("结果："):
                return clean
        return ""

    @staticmethod
    def _qzone_test_line_with_prefix(text: str, prefix: str) -> str:
        for line in str(text or "").replace("\r", "\n").split("\n"):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith(prefix):
                return clean
        return ""

    @staticmethod
    def _is_private_test_umo(umo: Any) -> bool:
        text = str(umo or "").strip()
        return bool(
            text
            and not re.search(r"(?:^|:)GroupMessage(?=:|$)", text, flags=re.IGNORECASE)
            and re.search(r"(?:^|:)FriendMessage(?=:|$)", text, flags=re.IGNORECASE)
        )
