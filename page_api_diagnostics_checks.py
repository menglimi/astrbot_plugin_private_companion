# -*- coding: utf-8 -*-
"""排障检查与事件域。

由 tools/split_mixin_domain.py 从 page_api_diagnostics.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 607 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from typing import Any



class PrivateCompanionPageApiDiagnosticsChecksMixin:
    """排障检查与事件域（从 PrivateCompanionPageApiDiagnosticsMixin 拆出）。"""


    def _diagnostic_envelope(
        self,
        result: dict[str, Any] | None,
        *,
        test_type: str = "",
        duration_ms: int = 0,
        test_id: str = "",
    ) -> dict[str, Any]:
        source = dict(result) if isinstance(result, dict) else {}
        contract_version = DIAGNOSTIC_ENVELOPE_VERSION
        plugin = getattr(self, "plugin", None)
        getter = getattr(plugin, "_diagnostic_operations_contract", None)
        if callable(getter):
            try:
                contract = getter()
                if isinstance(contract, dict):
                    contract_version = self._single_line(contract.get("version"), 60) or contract_version
            except Exception:
                pass
        source_request_id = self._single_line(
            source.get("request_id") or source.get("trace_id"),
            32,
        )
        resolved_test_id = test_id or self._single_line(source.get("test_id"), 100)
        if not resolved_test_id and re.fullmatch(r"[a-fA-F0-9]{12,32}", source_request_id):
            resolved_test_id = diagnostic_test_id(
                test_type or source.get("type"),
                token=source_request_id.lower(),
            )
        envelope = normalize_diagnostic_result(
            source,
            test_type=test_type,
            duration_ms=duration_ms,
            test_id=resolved_test_id,
            contract_version=contract_version,
        )

        # The public envelope intentionally strips arbitrary model output. The
        # local operations page still needs bounded, redacted delivery details
        # and the legacy request id to make a failed test actionable.
        for key, limit in (
            ("request_id", 32),
            ("trace_id", 32),
            ("test_status", 16),
            ("error_code", 40),
            ("code", 80),
            ("exception_type", 120),
            ("title", 80),
            ("delivery_umo", 180),
            ("called_plugin", 100),
            ("called_plugin_name", 100),
            ("called_plugin_version", 60),
            ("called_plugin_api_version", 80),
            ("called_plugin_status_schema", 80),
            ("availability_source", 80),
        ):
            if source.get(key) not in (None, ""):
                envelope[key] = self._single_line(source.get(key), limit)
        for key, limit in (
            ("error", 1600),
            ("delivery_error", 1200),
            ("detail", 1200),
            ("diagnostic_detail", 4000),
            ("suggestion", 600),
            ("next_step", 600),
        ):
            if source.get(key) not in (None, ""):
                envelope[key] = self._safe_test_diagnostic_text(source.get(key), limit)
        for key in ("generated", "delivered"):
            if key in source:
                envelope[key] = bool(source.get(key))
        if "unsupported" in source:
            envelope["unsupported"] = bool(source.get("unsupported"))

        warnings = source.get("warnings") if isinstance(source.get("warnings"), list) else []
        envelope["warnings"] = [
            text
            for item in warnings[:8]
            if (text := self._safe_test_diagnostic_text(item, 800))
        ]
        envelope["steps"] = [
            {
                "key": self._single_line(step.get("key"), 40),
                "name": self._single_line(step.get("name"), 60) or "执行阶段",
                "status": self._single_line(step.get("status"), 16) or "info",
                "detail": self._safe_test_diagnostic_text(step.get("detail"), 800),
                "elapsed_ms": self._int(step.get("elapsed_ms")),
            }
            for step in (source.get("steps") if isinstance(source.get("steps"), list) else [])[:24]
            if isinstance(step, dict)
        ]
        envelope["diagnostic_entries"] = [
            {
                "elapsed_ms": self._int(entry.get("elapsed_ms")),
                "level": self._single_line(entry.get("level"), 16),
                "stage": self._single_line(entry.get("stage"), 60),
                "message": self._safe_test_diagnostic_text(entry.get("message"), 1200),
            }
            for entry in (
                source.get("diagnostic_entries")
                if isinstance(source.get("diagnostic_entries"), list)
                else []
            )[:32]
            if isinstance(entry, dict)
        ]
        envelope["suggestions"] = [
            text
            for item in (
                source.get("suggestions")
                if isinstance(source.get("suggestions"), list)
                else []
            )[:12]
            if (text := self._safe_test_diagnostic_text(item, 220))
        ]
        envelope["sections"] = [
            {
                "key": self._single_line(section.get("key"), 40),
                "title": self._single_line(section.get("title"), 60),
                "local_count": self._int(section.get("local_count")),
                "model_count": self._int(section.get("model_count")),
                "suggestions": [
                    text
                    for item in (
                        section.get("suggestions")
                        if isinstance(section.get("suggestions"), list)
                        else []
                    )[:8]
                    if (text := self._safe_test_diagnostic_text(item, 220))
                ],
            }
            for section in (
                source.get("sections") if isinstance(source.get("sections"), list) else []
            )[:6]
            if isinstance(section, dict)
        ]
        for key in ("started_at", "finished_at"):
            if source.get(key) not in (None, ""):
                envelope[key] = self._float(source.get(key))
        return envelope

    def _troubleshooting_recent_events(
        self,
        *,
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        passive_no_reply: dict[str, Any] | None = None,
        persona_routing_warnings: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add(
            level: str,
            source: str,
            title: str,
            detail: str = "",
            *,
            ts: float = 0,
            action: str = "",
            jump: str = "",
            warning_type: str = "",
            warning_code: str = "",
        ) -> None:
            resolved_type = warning_type or (
                self._troubleshooting_semantic_warning_type(warning_code)
                if warning_code
                else self._troubleshooting_warning_type("event", source, title)
            )
            events.append(
                {
                    "level": level,
                    "source": source,
                    "title": self._single_line(title, 90),
                    "detail": self._single_line(detail, 220),
                    "action": self._single_line(action, 160),
                    "jump": self._single_line(jump, 40),
                    "ts": self._float(ts),
                    "time": self.plugin._format_timestamp_elapsed(ts) if ts else "",
                    "warning_type": resolved_type,
                    "warning_code": warning_code,
                }
            )

        for item in diagnostics:
            level = self._single_line(item.get("level"), 12)
            if level not in {"error", "warn"}:
                continue
            add(
                level,
                "配置诊断",
                item.get("title", ""),
                item.get("text", ""),
                action=item.get("action", ""),
                jump="troubleshooting",
                warning_type=self._single_line(item.get("warning_type"), 64),
                warning_code=self._single_line(item.get("warning_code"), 120),
            )

        for item in self._active_persona_routing_warnings(persona_routing_warnings)[:120]:
            if not isinstance(item, dict):
                continue
            requested = self._single_line(item.get("requested_persona_id"), 96) or "未解析"
            active = self._single_line(item.get("active_persona_id"), 96) or "未激活"
            reason = self._single_line(item.get("reason_code"), 100) or "unknown"
            count = max(1, self._int(item.get("count")))
            disposition = self._single_line(item.get("disposition"), 24) or "fallback"
            warning_code = self._single_line(item.get("code"), 120) or "persona.route.unknown"
            # An empty plugin-specific persona is valid in single-persona
            # mode: AstrBot's selected conversation persona is authoritative.
            # Hide records written by older versions so the panel does not
            # keep showing a resolved, non-actionable warning forever.
            if (
                warning_code == "persona.route.plugin_persona_unspecified"
                and not bool(getattr(self.plugin, "enable_multi_persona_mode", False))
            ):
                continue
            detail = "；".join(
                part
                for part in (
                    f"AstrBot 人格 {requested}",
                    f"插件人格 {active}",
                    f"原因 {reason}",
                    f"已合并 {count} 次" if count > 1 else "",
                    f"会话 {self._single_line(item.get('window_key'), 100)}" if item.get("window_key") else "",
                )
                if part
            )
            title = (
                "插件人格未指定"
                if warning_code == "persona.route.plugin_persona_unspecified"
                else "被动消息已回退主人格"
                if disposition.startswith("fallback")
                else "旧插件窗口绑定已忽略"
                if disposition == "ignored"
                else "主动消息人格校验未通过"
            )
            add(
                self._single_line(item.get("level"), 12) or "warn",
                "人格路由",
                title,
                detail,
                ts=self._float(item.get("last_ts")),
                action="检查 AstrBot 会话规则、已启用人格和人格配置健康状态",
                jump="config",
                warning_code=warning_code,
            )

        for item in proactive_tasks.get("audit_items", [])[:40]:
            status = self._single_line(item.get("status"), 24)
            if status not in {"failed", "dropped", "deferred"}:
                continue
            note = self._single_line(item.get("note"), 180)
            if status in {"dropped", "deferred"} and not self._proactive_audit_note_needs_troubleshooting(note):
                continue
            level = "error" if status == "failed" else "warn"
            title = item.get("topic") or item.get("reason") or item.get("note") or "主动执行异常"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                    item.get("text_preview") or "",
                ]
                if part
            )
            add(
                level,
                "主动审计",
                title,
                detail,
                ts=self._float(item.get("updated_ts") or item.get("created_ts")),
                jump="proactive",
                warning_code=self._troubleshooting_proactive_warning_code("audit", item, note),
            )

        for item in proactive_candidates.get("items", [])[:40]:
            if self._single_line(item.get("status"), 24) != "blocked":
                continue
            note = self._single_line(item.get("note"), 180)
            if self._proactive_candidate_block_is_normal(note):
                continue
            title = item.get("topic") or item.get("reason") or "主动候选被拦截"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                ]
                if part
            )
            add(
                "warn",
                "主动候选",
                title,
                detail,
                ts=self._float(item.get("last_seen_ts") or item.get("created_ts")),
                jump="proactive",
                warning_code=self._troubleshooting_proactive_warning_code("candidate", item, note),
            )

        for item in self._active_token_failures(token_stats.get("recent", []), limit=50):
            title = f"{self._token_task_label(item.get('task'))}失败"
            detail = "；".join(
                part
                for part in [
                    f"Provider {item.get('provider') or '-'}",
                    item.get("error") or "无错误详情",
                ]
                if part
            )
            add(
                "error",
                "模型调用",
                title,
                detail,
                ts=self._float(item.get("ts")),
                jump="tokens",
                warning_code=f"model_call.{re.sub(r'[^a-z0-9_]+', '_', self._single_line(item.get('task'), 40).lower()).strip('_') or 'unknown'}",
            )

        passive_items = passive_no_reply.get("items", []) if isinstance(passive_no_reply, dict) else []
        for item in passive_items[:40]:
            if not isinstance(item, dict):
                continue
            count = self._int(item.get("count"))
            source = self._single_line(item.get("source"), 40) or "被动未回复"
            reason = self._single_line(item.get("reason"), 100) or "未说明原因"
            inbound = self._single_line(item.get("last_inbound"), 100)
            detail = "；".join(
                part
                for part in [
                    f"已合并 {count} 次" if count > 1 else "最近 1 次",
                    f"会话 {self._single_line(item.get('last_session'), 80)}" if item.get("last_session") else "",
                    f"消息 {inbound}" if inbound else "",
                    self._single_line(item.get("last_detail"), 120),
                ]
                if part
            )
            add(
                self._single_line(item.get("level"), 12) or "info",
                source,
                reason,
                detail,
                ts=self._float(item.get("last_ts")),
                action="同类原因已合并计数，刷新后可查看最近样本",
                jump="troubleshooting",
                warning_code=f"passive_no_reply.{re.sub(r'[^a-z0-9_]+', '_', self._single_line(item.get('key'), 40).lower()).strip('_') or hashlib.sha256(reason.encode('utf-8')).hexdigest()[:12]}",
            )

        events.sort(key=lambda item: self._float(item.get("ts")), reverse=True)
        return events

    def _proactive_audit_note_needs_troubleshooting(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return False
        normal_tokens = (
            "主动行为失败或不适合发送",
            "不适合发送",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已有更早",
            "调度过滤",
            "已避开扎堆",
            "按日内节奏延后",
            "冷却",
            "间隔",
            "频率",
            "额度",
            "低分",
            "未达到阈值",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        if any(token in text for token in normal_tokens):
            return False
        error_tokens = (
            "异常",
            "报错",
            "Traceback",
            "Error",
            "Exception",
            "provider",
            "模型",
            "超时",
            "不可用",
            "发送失败",
            "生成失败",
            "调用失败",
            "保存失败",
            "处理失败",
            "database is locked",
        )
        return any(token in text for token in error_tokens)

    def _troubleshooting_checks(
        self,
        *,
        data: dict[str, Any],
        users: dict[str, Any],
        groups: dict[str, Any],
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        cache: dict[str, Any],
        tts: dict[str, Any],
        sqlite_status: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def add(level: str, title: str, text: str, action: str = "", jump: str = "", warning_code: str = "") -> None:
            item = {
                "level": level,
                "title": self._single_line(title, 90),
                "text": self._single_line(text, 240),
                "action": self._single_line(action, 180),
                "jump": self._single_line(jump, 40),
                "warning_code": warning_code,
                "warning_type": self._troubleshooting_semantic_warning_type(warning_code)
                if warning_code
                else self._troubleshooting_warning_type("check", title),
            }
            checks.append(item)

        llm_blocks = []
        raw_llm_blocks = data.get("group_llm_reply_blocks")
        if isinstance(raw_llm_blocks, dict):
            for group_id, item in raw_llm_blocks.items():
                if not isinstance(item, dict) or not bool(item.get("enabled")):
                    continue
                gid = self._single_line(item.get("group_id") or group_id, 80)
                if not gid:
                    continue
                group = groups.get(gid) if isinstance(groups, dict) else None
                name = self._single_line((group or {}).get("name") if isinstance(group, dict) else "", 40)
                llm_blocks.append({"group_id": gid, "name": name, "updated_at": item.get("updated_at")})
        if llm_blocks:
            first = llm_blocks[0]
            label = f"{first.get('name')}({first.get('group_id')})" if first.get("name") else str(first.get("group_id") or "-")
            add(
                "warn",
                "存在群级 LLM 回复熔断",
                f"{len(llm_blocks)} 个群已关闭所有 LLM 回复，首个：{label}。这是手动开关状态，不会被最近记录清理。",
                "在对应群发送：陪伴群 开启LLM",
                "troubleshooting",
                "group.llm_breaker",
            )

        enabled_users = [
            item
            for item in users.values()
            if isinstance(item, dict)
            and (
                item.get("proactive_private_enabled") is True
                or (
                    isinstance(item.get("unified_profile_capabilities"), dict)
                    and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                )
            )
        ]
        runtime = proactive_tasks.get("runtime", {})
        daily_limit = self._int(getattr(self.plugin, "max_daily_messages", 0))
        budget = token_stats.get("budget", {})
        if not enabled_users:
            add("warn", "主动消息没有私聊对象", "当前没有启用的私聊对象，主动消息不会有目标。", "到私聊页新增或启用对象", "private", "proactive.no_enabled_users")
        elif daily_limit <= 0:
            add("warn", "私聊主动总额度为 0", "每日主动上限为 0 时，主动念头、候选、主动行为生成与发送均已停止。", "到模块配置调高每日主动上限", "modules", "proactive.daily_limit_zero")
        elif not runtime.get("healthy"):
            add("warn", "主动循环心跳不新鲜", runtime.get("last_tick_error") or "最近没有检测到主动循环心跳。", "查看主动页的循环状态", "proactive", "proactive.loop_stale")
        else:
            add("ok", "主动循环可运行", f"启用对象 {len(enabled_users)} 个，最近心跳 {runtime.get('last_tick_started') or '-'}。", "", "proactive")

        if budget.get("exceeded"):
            add("error", "今日 Token 硬限额已耗尽", f"今日已用 {budget.get('used')}，硬限额 {budget.get('limit')}。", "调高每日 Token 限额或等待明日重置", "tokens")
        elif budget.get("soft_active"):
            add("warn", "Token 软限额正在暂缓后台任务", f"今日已用 {budget.get('used')}，软限额 {budget.get('soft_limit')}；主动生图、新闻、创作等低优先级任务会延后。", "到 Token 页或模块配置检查限额", "tokens", "token.soft_limit_active")
        else:
            add("ok", "Token 预算未阻塞", f"今日已用 {budget.get('used', 0)}；软限额剩余 {budget.get('soft_remaining') if budget.get('soft_remaining') is not None else '不限'}。", "", "tokens")

        photo_enabled = bool(getattr(self.plugin, "enable_photo_text_action", False))
        photo_available = bool(getattr(self.plugin, "_photo_text_available", lambda *args, **kwargs: False)())
        proactive_scope_limit_getter = getattr(self.plugin, "_photo_generation_scope_daily_limit", None)
        proactive_scope_limit = (
            self._int(proactive_scope_limit_getter("proactive"), -1, -1, 100)
            if callable(proactive_scope_limit_getter)
            else self._int(getattr(self.plugin, "photo_generation_proactive_max_daily", -1), -1, -1, 100)
        )
        proactive_scope_unlimited = proactive_scope_limit < 0
        photo_blocked = [
            item for item in proactive_candidates.get("items", [])
            if "photo_text" in str(item.get("action") or "") and str(item.get("status") or "") == "blocked"
        ]
        photo_blocked_abnormal = [
            item for item in photo_blocked
            if not self._proactive_candidate_block_is_normal(self._single_line(item.get("note"), 180))
        ]
        if not photo_enabled:
            add("warn", "主动带图功能未开启", "enable_photo_text_action 关闭时不会生成主动图片。", "到功能开关打开主动拍照/生图", "config", "image.proactive_disabled")
        elif not photo_available:
            if proactive_scope_unlimited:
                add(
                    "warn",
                    "主动带图当前不可用",
                    "Bot 主动生图范围额度为不限量（-1）；当前阻塞不是该范围额度耗尽，"
                    "请继续检查生图后端、Token 软限额、每用户主动带图上限或用户关系角色。",
                    "检查生图后端、Token 预算和“每日主动带图上限”",
                    "modules",
                    "image.proactive_backend_unavailable",
                )
            else:
                add(
                    "warn",
                    "主动带图当前不可用",
                    f"Bot 主动生图范围额度为 {proactive_scope_limit}；可能是范围额度已用完、生图后端不可用，"
                    "Token 软限额暂缓，或当前对象不允许 photo_text。",
                    "检查生图后端、每日生图上限和用户关系角色",
                    "modules",
                    "image.proactive_backend_unavailable",
                )
        elif photo_blocked_abnormal:
            add("warn", "近期带图候选被拦截", self._single_line(photo_blocked_abnormal[0].get("note"), 160) or "最近 photo_text 候选没有进入发送。", "到主动页筛选 photo_text", "proactive", "image.proactive_candidate_blocked")
        else:
            add("ok", "主动带图链路可尝试", "开关和可用性检查通过；是否出现取决于主动动机、天气/日程和候选权重。", "", "proactive")

        if bool(tts.get("enhancement_enabled")):
            if bool(tts.get("provider_available")):
                add("ok", "TTS 合成后端可用", f"模式 {tts.get('mode')}，语种 {tts.get('language')}，后端 {tts.get('provider_label') or '-'}。", "", "modules")
            else:
                add("warn", "TTS 强化开启但合成后端不可用", "插件能处理 TTS 标签，但当前选择的 AstrBot TTS Provider 或 MiMo Voice Clone 联动不可用。", "检查 TTS 合成后端；MiMo 模式需启用目标插件并保留 mimo_tts_speak 工具", "modules", "tts.provider_unavailable")
        else:
            add("info", "TTS 强化未开启", "模型不应被要求生成 TTS 标签；如仍出现标签，发送前会清理。", "", "modules")

        sqlite_bad = [item for item in sqlite_status.get("items", []) if item.get("level") in {"warn", "error"}]
        if sqlite_bad:
            add("warn", "SQLite 并发状态需要关注", sqlite_bad[0].get("text") or "有数据库未处于 WAL 或检查失败。", "重启插件后查看是否仍有 database is locked", "troubleshooting", "sqlite.wal")
        else:
            add("ok", "SQLite WAL 检查通过", f"已检查 {len(sqlite_status.get('items', []))} 个数据库文件。", "", "troubleshooting")

        token_errors = self._active_token_failures(token_stats.get("recent", []))
        if token_errors:
            first = token_errors[0]
            add("error", "最近存在模型调用失败", first.get("error") or f"{first.get('task') or '任务'} 调用失败。", "到 Token 页查看失败任务和 provider", "tokens")
        else:
            add("ok", "近期模型调用无待处理失败", "近 30 分钟内没有未恢复的模型调用失败；历史失败可在 Token 页查看。", "", "tokens")

        image_cache = cache.get("private_image_vision", {})
        if image_cache.get("enabled"):
            add("ok", "图片视觉缓存已开启", f"当前缓存 {image_cache.get('items', 0)}/{image_cache.get('max_items') or '不限'} 条。", "", "image-cache")
        else:
            add("info", "图片视觉缓存未开启", "重复表情包会重复调用视觉模型，但不影响首次识图。", "到模块配置开启重复图片缓存", "modules")
        provider_runtime = image_cache.get("provider_runtime") if isinstance(image_cache.get("provider_runtime"), dict) else {}
        provider_candidates = provider_runtime.get("candidates") if isinstance(provider_runtime.get("candidates"), list) else []
        usable_vision = [
            item for item in provider_candidates
            if isinstance(item, dict) and item.get("available") and item.get("supports_image") and not item.get("cooldown")
        ]
        provider_cooldowns = provider_runtime.get("cooldowns") if isinstance(provider_runtime.get("cooldowns"), list) else []
        last_success = provider_runtime.get("last_success") if isinstance(provider_runtime.get("last_success"), dict) else {}
        vision_priority = self._single_line(provider_runtime.get("priority"), 40) or "astrbot_first"
        vision_priority_label = {
            "astrbot_first": "AstrBot 图片转文字优先",
            "plugin_first": "插件识图模型优先",
            "recent_success_first": "近期成功模型优先",
        }.get(vision_priority, "AstrBot 图片转文字优先")
        if provider_runtime.get("error"):
            add("warn", "私聊图片识别调度状态读取失败", provider_runtime.get("error") or "无法读取当前视觉模型状态。", "刷新排障页或查看日志", "troubleshooting", "vision.runtime_unreadable")
        elif not usable_vision:
            add(
                "warn",
                "私聊图片识别暂无可用模型",
                f"候选 {len(provider_candidates)} 个，但没有同时满足可用、支持图片且不在冷却的模型。",
                "检查快速配置/精准配置里的插件识图模型，或等待临时降权结束",
                "config",
                "vision.no_available_provider",
            )
        elif last_success.get("provider_id"):
            add(
                "ok",
                f"私聊图片识别：{vision_priority_label}",
                f"最近成功视觉模型：{last_success.get('provider_id')}（{last_success.get('source') or '来源未知'}，{last_success.get('time') or '刚刚'}）；当前可用 {len(usable_vision)} 个。首选失败时会继续切换后续候选。",
                "",
                "troubleshooting",
            )
        else:
            first_provider = usable_vision[0].get("provider_id") if usable_vision and isinstance(usable_vision[0], dict) else "-"
            add("info", "私聊图片识别候选模型可用", f"当前可用 {len(usable_vision)} 个，首选 {first_provider}；成功一次后会记录为视觉恢复候选。", "", "troubleshooting")
        if provider_cooldowns:
            first_cooldown = provider_cooldowns[0] if isinstance(provider_cooldowns[0], dict) else {}
            add(
                "warn",
                "有识图模型被临时降权",
                f"{first_cooldown.get('provider_id') or '-'}：{first_cooldown.get('error') or '最近调用失败'}；到期 {first_cooldown.get('until') or '-'}。",
                "如果反复出现，换掉插件识图模型或调高单次超时",
                "config",
                "vision.provider_cooldown",
            )

        diag_warns = [item for item in diagnostics if item.get("level") in {"warn", "error"}]
        if diag_warns:
            add("warn", "配置诊断仍有待处理项", f"{len(diag_warns)} 项需要关注：{diag_warns[0].get('title') or '-'}。", diag_warns[0].get("action") or "查看下方最近异常", "troubleshooting", "diagnostic.pending")
        else:
            add("ok", "配置诊断无待处理警告", "现有未屏蔽诊断项没有 warn/error。", "", "dashboard")
        return checks
