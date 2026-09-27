# -*- coding: utf-8 -*-
"""persona 配置入口与风格生成流程域。

由 tools/split_mixin_domain.py 从 page_api_persona_config.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 721 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiPersonaConfigMixin）。
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from .page_api_shared import _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiPersonaFlowMixin:
    """persona 配置入口与风格生成流程域（从 PrivateCompanionPageApiPersonaConfigMixin 拆出）。"""


    async def update_personal_goal(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        goal_id = self._single_line(payload.get("id"), 40)
        title = self._single_line(payload.get("title"), 60)
        if not goal_id and not title:
            return self._error("缺少目标名称")

        def parse_terms(value: Any) -> list[str]:
            raw = value if isinstance(value, list) else re.split(r"[,，、\n]+", str(value or ""))
            terms: list[str] = []
            for item in raw:
                term = self._single_line(item, 32)
                if term and term not in terms:
                    terms.append(term)
            return terms[:16]

        try:
            async with self.plugin._data_lock:
                goals = self.plugin.data.setdefault("personal_goals", [])
                if not isinstance(goals, list):
                    goals = []
                    self.plugin.data["personal_goals"] = goals
                index = next(
                    (idx for idx, item in enumerate(goals) if isinstance(item, dict) and self._single_line(item.get("id"), 40) == goal_id),
                    -1,
                )
                if payload.get("delete"):
                    if index < 0:
                        return self._error("没有找到要删除的个人目标，请刷新后重试")
                    goals.pop(index)
                    self.plugin._save_data_sync(sections={"personal_goals"})
                    return self._ok({"changed": True, "message": "已删除个人目标", "personal_goals": self._personal_goal_summary(self.plugin.data)})
                if not title:
                    return self._error("缺少目标名称")
                category = self._single_line(payload.get("category"), 24) or "生活"
                if any(token in category for token in ("创作", "写作", "绘画创作", "作品")):
                    return self._error("创作型目标请继续使用创作项目，这里只管理非创作型个人目标")
                existing = goals[index] if index >= 0 and isinstance(goals[index], dict) else {}
                if index < 0 and any(self._single_line(item.get("title"), 60) == title for item in goals if isinstance(item, dict)):
                    return self._error("已经存在同名个人目标")
                status = self.plugin._personal_goal_status(payload.get("status") or existing.get("status"))
                old_progress = max(0, min(100, self._int(existing.get("progress"))))
                progress = max(0, min(100, self._int(payload.get("progress")) if payload.get("progress") is not None else self._int(existing.get("progress"))))
                if status == "completed":
                    progress = 100
                elif progress >= 100:
                    status = "completed"
                now = time.time()
                keywords = parse_terms(payload.get("keywords")) if "keywords" in payload else parse_terms(existing.get("keywords"))
                if index < 0 and not keywords:
                    keywords = [title]
                goal = dict(existing)
                goal.update(
                    {
                        "id": goal_id or uuid.uuid4().hex[:12],
                        "title": title,
                        "category": category,
                        "status": status,
                        "progress": progress,
                        "next_step": self._single_line(payload.get("next_step") if "next_step" in payload else existing.get("next_step"), 100),
                        "note": self._single_line(payload.get("note") if "note" in payload else existing.get("note"), 160),
                        "keywords": keywords,
                        "auto_step": max(1, min(50, self._int(payload.get("auto_step")) or self._int(existing.get("auto_step")) or 10)),
                        "created_at": self._float(existing.get("created_at")) or now,
                        "updated_at": now,
                        "last_progress_at": self._float(existing.get("last_progress_at")) or now,
                        "recent_logs": existing.get("recent_logs") if isinstance(existing.get("recent_logs"), list) else [],
                    }
                )
                if progress > old_progress:
                    goal["last_progress_at"] = now
                    goal["stalled_notified_at"] = 0
                    logs = goal.setdefault("recent_logs", [])
                    if not isinstance(logs, list):
                        logs = []
                        goal["recent_logs"] = logs
                    logs.append({"ts": now, "kind": "manual_progress", "progress": progress, "evidence": "在陪伴面板中手动更新"})
                    del logs[:-12]
                    if progress >= 100:
                        goal["pending_share_event"] = {"kind": "completed", "evidence": "在陪伴面板中手动更新为已完成"}
                    elif progress // 25 > old_progress // 25:
                        goal["pending_share_event"] = {"kind": "progress", "milestone": (progress // 25) * 25, "evidence": "在陪伴面板中手动更新进度"}
                elif progress < old_progress:
                    goal.pop("pending_share_event", None)
                if status in {"paused", "abandoned"}:
                    goal.pop("pending_share_event", None)
                if status == "completed" and not self._float(goal.get("completed_at")):
                    goal["completed_at"] = now
                elif status != "completed":
                    goal["completed_at"] = 0
                if index >= 0:
                    goals[index] = goal
                else:
                    goals.append(goal)
                self.plugin._save_data_sync(sections={"personal_goals"})
                return self._ok({"message": "已保存个人目标", "personal_goals": self._personal_goal_summary(self.plugin.data)})
        except Exception as exc:
            logger.error(f"更新个人目标失败: {exc}", exc_info=True)
            return self._exception_error("更新个人目标失败")

    async def list_roleplay_personas(self) -> dict[str, Any]:
        try:
            reconcile = getattr(self.plugin, "_reconcile_deleted_personas_async", None)
            if callable(reconcile):
                # AstrBot may delete personas while the plugin remains loaded;
                # reconcile before projecting the list so stale plugin-only
                # entries do not reappear in the WebUI.
                await reconcile()
            items = await self._roleplay_persona_items()
            enabled = bool(getattr(self.plugin, "enable_multi_persona_mode", False))
            if enabled:
                configured_ids = getattr(self.plugin, "_persona_profile_ids", lambda: [])()
                known = {str(item.get("id") or "") for item in items if isinstance(item, dict)}
                for pid in configured_ids:
                    if pid and pid not in known:
                        items.append({"id": pid, "label": pid, "source": "独立资料", "is_default": False})
            primary_getter = getattr(self.plugin, "_primary_persona_id", None)
            current = self._single_line(
                primary_getter()
                if callable(primary_getter)
                else getattr(self.plugin, "plugin_specific_persona_id", ""),
                120,
            )
            default_id = ""
            for item in items:
                if item.get("is_default"):
                    default_id = str(item.get("id") or "")
                    break
            response = {
                "items": items,
                "current": current or default_id,
                "default": default_id,
            }
            if enabled:
                status_getter = getattr(self.plugin, "_multi_persona_status", None)
                response["multi_persona"] = (
                    status_getter()
                    if callable(status_getter)
                    else {
                        "enabled": enabled,
                        "primary": current,
                        "profiles": configured_ids,
                        "window_bindings": {},
                        "routing_authority": "astrbot",
                    }
                )
                response["multi_persona"]["current"] = current or default_id
            return self._ok(response)
        except Exception as exc:
            logger.warning(f"获取人格列表失败: {exc}", exc_info=True)
            return self._ok({"items": self._fallback_roleplay_persona_items(), "current": "", "default": ""})

    async def get_persona_config_state(self) -> dict[str, Any]:
        persona_id = self._single_line(request.args.get("persona_id"), 96)
        getter = getattr(self.plugin, "_persona_config_state", None)
        if not callable(getter):
            return self._error("当前版本不支持人格独立配置", status_code=503)
        try:
            return self._ok(getter(persona_id))
        except Exception as exc:
            return self._error(str(exc))

    async def create_persona_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        creator = getattr(self.plugin, "_create_persona_config_async", None)
        if not callable(creator):
            return self._error("当前版本不支持创建人格配置", status_code=503)
        result = await creator(
            payload.get("persona_id"),
            bot_name=payload.get("bot_name"),
            mode=payload.get("mode") or "follow_primary",
            source_persona_id=payload.get("source_persona_id"),
            recovery=bool(payload.get("recovery")),
        )
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "创建人格配置失败", status_code=int(result.get("status_code") or 400))

    async def update_persona_settings(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        updater = getattr(self.plugin, "_update_persona_settings_async", None)
        if not callable(updater):
            return self._error("当前版本不支持人格配置更新", status_code=503)
        changes = payload.get("changes") or {}
        if isinstance(changes, dict):
            changes = {
                str(key): self._normalize_setting_value(str(key), value)
                for key, value in changes.items()
            }
        result = await updater(
            payload.get("persona_id"),
            changes=changes,
            follow_primary_keys=payload.get("follow_primary_keys") or [],
            expected_revision=payload.get("expected_revision"),
        )
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "人格配置更新失败", status_code=int(result.get("status_code") or 400))

    async def preview_persona_config_detach(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        previewer = getattr(self.plugin, "_persona_detach_preview", None)
        if not callable(previewer):
            return self._error("当前版本不支持脱离主人格", status_code=503)
        result = previewer(payload.get("persona_id"))
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "脱离预览失败")

    async def apply_persona_config_detach(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        apply_detach = getattr(self.plugin, "_detach_persona_settings_async", None)
        if not callable(apply_detach):
            return self._error("当前版本不支持脱离主人格", status_code=503)
        result = await apply_detach(
            payload.get("persona_id"),
            expected_revision=payload.get("expected_revision"),
            preview_hash=payload.get("preview_hash"),
        )
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "脱离主人格失败", status_code=int(result.get("status_code") or 400))

    async def migrate_persona_profile(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        if not bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
            return self._ok({"enabled": False, "migrated": False})
        source_id = str(payload.get("source_persona_id") or "").strip()
        target_id = str(payload.get("target_persona_id") or "").strip()
        keys = payload.get("keys") if isinstance(payload.get("keys"), list) else []
        migrator = getattr(self.plugin, "_migrate_persona_profile_async", None)
        result = (
            await migrator(source_id, target_id, keys)
            if callable(migrator)
            else self.plugin._migrate_persona_profile(source_id, target_id, keys)
        )
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "人格资料迁移失败")

    async def reset_current_persona(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        persona_id = self._single_line(payload.get("persona_id"), 120)
        resetter = getattr(self.plugin, "_reset_current_persona_store", None)
        if not callable(resetter):
            return self._error("当前版本不支持重置人格资料")
        try:
            result = await resetter(persona_id, rebuild_today=True)
        except Exception as exc:
            logger.warning(
                "重置当前人格失败 persona=%s error=%s",
                persona_id or "single",
                self._single_line(exc, 180),
                exc_info=True,
            )
            return self._exception_error("重置当前人格失败")
        return self._ok(result) if result.get("ok") else self._error(result.get("message") or "重置当前人格失败")

    async def standardize_persona_from_questionnaire(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        umo = self._single_line(payload.get("umo"), 220)
        persona_id = self._single_line(payload.get("persona_id"), 120)
        questionnaire = payload.get("questionnaire") if isinstance(payload.get("questionnaire"), dict) else {}
        source_override_raw = str(payload.get("source_text") or "")
        supplement_text_raw = str(questionnaire.get("supplement_text") or "") if isinstance(questionnaire, dict) else ""
        source_override = self._multi_line_head_tail(source_override_raw, 30000)
        try:
            persona_prompt = source_override
            effective_persona_id = persona_id
            if not persona_prompt:
                persona_prompt, effective_persona_id = await self._roleplay_persona_prompt_for_id(persona_id, umo)
            persona_prompt = str(persona_prompt or "").strip()
            if not persona_prompt or persona_prompt.startswith("未读取到 AstrBot 默认人格"):
                return self._error("还没有读取到可用的 AstrBot 人格文本，可以先让 Bot 触发一次对话，或在问卷里粘贴原人格")
            caller = getattr(self.plugin, "_llm_call", None)
            if not callable(caller):
                return self._error("当前插件运行态无法调用模型")
            provider_id = self._standardize_persona_provider_id()
            system_prompt, user_prompt = self._persona_standardization_prompt(persona_prompt, questionnaire)
            strength = self._single_line(questionnaire.get("strength"), 40) if isinstance(questionnaire, dict) else ""
            input_chars = len(persona_prompt) + len(supplement_text_raw)
            draft_max_tokens = 4200
            if input_chars > 8000:
                draft_max_tokens = 5600
            if input_chars > 16000:
                draft_max_tokens = 7000
            if strength == "deep":
                draft_max_tokens = min(8200, draft_max_tokens + 1000)
            elif strength == "light":
                draft_max_tokens = min(draft_max_tokens, 4200)
            raw = await caller(
                user_prompt,
                max_tokens=draft_max_tokens,
                provider_id=provider_id,
                task="persona_standardization_questionnaire",
                system_prompt=system_prompt,
            )
            parse_note = ""
            repair_provider_id = ""
            if raw is None:
                draft = self._fallback_persona_standardization_result(persona_prompt, questionnaire, "模型调用返回空结果")
                parse_note = "模型调用未返回结果，已生成本地兜底审核稿。"
                raw_preview_source = ""
            else:
                raw_preview_source = raw
                try:
                    parsed = self._loads_json_object(raw)
                except Exception as parse_exc:
                    repair_provider_id = self._roleplay_draft_repair_provider_id(provider_id)
                    if repair_provider_id:
                        try:
                            repair_system, repair_user = self._persona_standardization_repair_prompt(raw)
                            repair_raw = await caller(
                                repair_user,
                                max_tokens=max(3600, min(draft_max_tokens, 7000)),
                                provider_id=repair_provider_id,
                                task="persona_standardization_json_repair",
                                system_prompt=repair_system,
                            )
                            if repair_raw is None:
                                raise ValueError("修复模型返回空结果")
                            parsed = self._loads_json_object(repair_raw)
                            raw_preview_source = repair_raw
                            parse_note = f"初次返回无法解析，已使用 {repair_provider_id} 修复为 JSON。"
                        except Exception as repair_exc:
                            logger.warning(
                                "人格标准化 JSON 修复失败: %s；初次错误: %s",
                                self._single_line(repair_exc, 160),
                                self._single_line(parse_exc, 160),
                                exc_info=True,
                            )
                            parsed = self._fallback_persona_standardization_result(persona_prompt, questionnaire, parse_exc)
                            parse_note = "模型未返回可解析 JSON，已生成本地兜底审核稿。"
                    else:
                        parsed = self._fallback_persona_standardization_result(persona_prompt, questionnaire, parse_exc)
                        parse_note = "模型未返回可解析 JSON，已生成本地兜底审核稿。"
                draft = self._normalize_persona_standardization_result(parsed)
                if not str(draft.get("template") or "").strip():
                    draft = self._fallback_persona_standardization_result(persona_prompt, questionnaire, "模型返回空模板")
                    parse_note = f"{parse_note} 模型返回模板为空，已生成本地兜底审核稿。".strip()
                min_template_chars = self._persona_standardization_min_template_chars(input_chars, len(supplement_text_raw))
                if (
                    min_template_chars
                    and len(str(draft.get("template") or "")) < min_template_chars
                    and "兜底审核稿" not in parse_note
                ):
                    try:
                        expand_system, expand_user = self._persona_standardization_expand_prompt(
                            persona_prompt,
                            questionnaire,
                            str(draft.get("template") or ""),
                            min_template_chars=min_template_chars,
                        )
                        expand_raw = await caller(
                            expand_user,
                            max_tokens=max(5600, min(9000, draft_max_tokens + 1200)),
                            provider_id=provider_id,
                            task="persona_standardization_expand",
                            system_prompt=expand_system,
                        )
                        if expand_raw:
                            try:
                                expanded_parsed = self._loads_json_object(expand_raw)
                            except Exception:
                                expand_repair_provider_id = self._roleplay_draft_repair_provider_id(provider_id)
                                if not expand_repair_provider_id:
                                    raise
                                expand_repair_system, expand_repair_user = self._persona_standardization_repair_prompt(expand_raw)
                                expand_repair_raw = await caller(
                                    expand_repair_user,
                                    max_tokens=max(5200, min(9000, draft_max_tokens + 1000)),
                                    provider_id=expand_repair_provider_id,
                                    task="persona_standardization_expand_json_repair",
                                    system_prompt=expand_repair_system,
                                )
                                expanded_parsed = self._loads_json_object(expand_repair_raw)
                                repair_provider_id = repair_provider_id or expand_repair_provider_id
                                raw_preview_source = expand_repair_raw
                            else:
                                raw_preview_source = expand_raw
                            expanded_draft = self._normalize_persona_standardization_result(expanded_parsed)
                            if len(str(expanded_draft.get("template") or "")) > len(str(draft.get("template") or "")) + 300:
                                draft = expanded_draft
                                parse_note = f"{parse_note} 初稿过短，已根据长参考自动扩写基础设定审核稿。".strip()
                            else:
                                parse_note = f"{parse_note} 初稿偏短，已尝试扩写；请重点审核参考资料是否被充分吸收。".strip()
                    except Exception as expand_exc:
                        logger.warning(
                            "人格标准化薄稿扩写失败: %s",
                            self._single_line(expand_exc, 180),
                            exc_info=True,
                        )
                        parse_note = f"{parse_note} 初稿偏短，但自动扩写失败，请手动补充或重试。".strip()
            return self._ok(
                {
                    "draft": draft,
                    "provider_id": provider_id,
                    "provider_role": self._roleplay_provider_role(provider_id),
                    "repair_provider_id": repair_provider_id,
                    "repair_provider_role": self._roleplay_provider_role(repair_provider_id),
                    "parse_note": parse_note,
                    "persona_id": effective_persona_id or self._single_line(getattr(self.plugin, "plugin_specific_persona_id", ""), 120),
                    "source_chars": len(persona_prompt),
                    "supplement_chars": len(supplement_text_raw),
                    "input_chars": input_chars,
                    "max_tokens": draft_max_tokens,
                    "source_preview": self._single_line(persona_prompt, 260),
                    "raw_preview": self._single_line(raw_preview_source, 300),
                    "review_required": True,
                    "apply_supported": False,
                    "apply_note": "当前版本只生成可审核草稿，不自动覆盖 AstrBot 人格。请审核后复制到 AstrBot 人格配置。",
                }
            )
        except Exception as exc:
            logger.error(f"人格标准化问卷生成失败: {exc}", exc_info=True)
            return self._exception_error("人格标准化问卷生成失败")

    async def generate_persona_style_scenarios(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        base_template = self._multi_line(payload.get("base_template"), 12000)
        questionnaire = payload.get("questionnaire") if isinstance(payload.get("questionnaire"), dict) else {}
        timeout_seconds = self._float(payload.get("timeout_seconds"), 40.0, 15.0, 120.0)
        batch_size = self._int(payload.get("batch_size"), 3, 1, 10)
        scenario_offset = self._int(payload.get("scenario_offset"), 0, 0, 1000)
        scenario_limit = self._int(payload.get("scenario_limit"), 0, 0, 24)
        try:
            if not base_template:
                return self._error("请先生成并确认基础设定稿")
            all_scenario_specs = self._persona_style_scenario_specs()
            scenario_total = len(all_scenario_specs)
            if scenario_limit > 0:
                scenario_specs = all_scenario_specs[scenario_offset : scenario_offset + scenario_limit]
            else:
                scenario_specs = all_scenario_specs
                scenario_offset = 0
                scenario_limit = scenario_total
            next_offset = min(scenario_total, scenario_offset + len(scenario_specs))
            has_more = next_offset < scenario_total
            if not scenario_specs:
                return self._ok(
                    {
                        "draft": self._normalize_persona_style_scenarios_result(
                            {
                                "scenarios": [],
                                "style_summary": "全部情景候选已生成。",
                                "warnings": [],
                                "review_checklist": ["每个情景选择最贴近的一句，或在自填框里改成更像角色的话。"],
                            }
                        ),
                        "provider_id": "",
                        "provider_role": "",
                        "repair_provider_id": "",
                        "repair_provider_role": "",
                        "parse_note": "",
                        "raw_preview": "",
                        "timeout_seconds": int(timeout_seconds),
                        "batch_size": batch_size,
                        "scenario_offset": scenario_offset,
                        "scenario_limit": scenario_limit,
                        "scenario_total": scenario_total,
                        "next_offset": next_offset,
                        "has_more": False,
                        "review_required": True,
                        "apply_supported": False,
                    }
                )
            caller = getattr(self.plugin, "_llm_call", None)
            if not callable(caller):
                return self._error("当前插件运行态无法调用模型")
            provider_id = self._persona_style_scenarios_provider_id()
            batches = [
                (batch_index, scenario_specs[start_index : start_index + batch_size])
                for batch_index, start_index in enumerate(range(0, len(scenario_specs), batch_size), 1)
            ]
            batch_timeout = max(10.0, min(28.0, timeout_seconds - 4.0))
            raw_preview_source = ""
            repair_provider_id = ""

            async def run_batch(batch_index: int, batch_specs: list[tuple[str, str, str, str]]) -> dict[str, Any]:
                system_prompt, user_prompt = self._persona_style_scenarios_prompt(base_template, questionnaire, specs=batch_specs)
                max_tokens = max(1200, min(2400, 380 + len(batch_specs) * 300))
                local_repair_provider_id = ""
                try:
                    raw = await asyncio.wait_for(
                        caller(
                            user_prompt,
                            max_tokens=max_tokens,
                            provider_id=provider_id,
                            task=f"persona_style_scenarios_batch_{batch_index}",
                            system_prompt=system_prompt,
                        ),
                        timeout=batch_timeout,
                    )
                    if raw is None:
                        raise ValueError("模型调用返回空结果")
                    try:
                        parsed = self._loads_json_object(raw)
                    except Exception as parse_exc:
                        local_repair_provider_id = self._roleplay_draft_repair_provider_id(provider_id)
                        if not local_repair_provider_id:
                            raise parse_exc
                        repair_system, repair_user = self._persona_style_scenarios_repair_prompt(raw)
                        repair_raw = await asyncio.wait_for(
                            caller(
                                repair_user,
                                max_tokens=max(800, min(1400, max_tokens)),
                                provider_id=local_repair_provider_id,
                                task=f"persona_style_scenarios_json_repair_{batch_index}",
                                system_prompt=repair_system,
                            ),
                            timeout=8.0,
                        )
                        if repair_raw is None:
                            raise ValueError("修复模型返回空结果")
                        parsed = self._loads_json_object(repair_raw)
                        raw = repair_raw
                    normalized = self._normalize_persona_style_scenarios_result(parsed)
                    batch_scenarios = self._align_persona_style_scenario_batch(batch_specs, normalized.get("scenarios", []), base_template)
                    return {
                        "scenarios": batch_scenarios,
                        "warnings": normalized.get("warnings", []),
                        "review_checklist": normalized.get("review_checklist", []),
                        "raw_preview": str(raw or ""),
                        "repair_provider_id": local_repair_provider_id,
                        "fallback": False,
                    }
                except asyncio.TimeoutError:
                    fallback = self._fallback_persona_style_scenarios_result(base_template, "", specs=batch_specs, include_warning=False)
                    return {
                        "scenarios": fallback.get("scenarios", []),
                        "warnings": [],
                        "review_checklist": [],
                        "raw_preview": "",
                        "fallback": True,
                        "reason": f"第 {batch_index} 批超过 {batch_timeout:.0f} 秒",
                    }
                except Exception as batch_exc:
                    logger.warning(
                        "人格风格试答第 %s 批失败: %s",
                        batch_index,
                        self._single_line(batch_exc, 180),
                        exc_info=True,
                    )
                    fallback = self._fallback_persona_style_scenarios_result(base_template, "", specs=batch_specs, include_warning=False)
                    return {
                        "scenarios": fallback.get("scenarios", []),
                        "warnings": [],
                        "review_checklist": [],
                        "raw_preview": "",
                        "fallback": True,
                        "reason": f"第 {batch_index} 批失败",
                    }

            batch_results = await asyncio.gather(*(run_batch(batch_index, batch_specs) for batch_index, batch_specs in batches))
            scenario_items: list[dict[str, Any]] = []
            warnings: list[str] = []
            review_checklist: list[str] = []
            fallback_reasons: list[str] = []
            for batch_result in batch_results:
                scenario_items.extend(batch_result.get("scenarios", []))
                warnings.extend(batch_result.get("warnings", []))
                review_checklist.extend(batch_result.get("review_checklist", []))
                raw_preview_source = raw_preview_source or str(batch_result.get("raw_preview") or "")
                repair_provider_id = repair_provider_id or str(batch_result.get("repair_provider_id") or "")
                if batch_result.get("fallback"):
                    fallback_reasons.append(self._single_line(batch_result.get("reason"), 60) or "部分批次")
            result = self._normalize_persona_style_scenarios_result(
                {
                    "scenarios": scenario_items,
                    "style_summary": (
                        f"已生成第 {scenario_offset + 1}-{next_offset} 个情景候选。"
                        if scenario_limit > 0 and scenario_total > len(scenario_specs)
                        else "已生成情景候选；慢批次会自动使用本地候选补齐，可对不满意的单项重生成。"
                    ),
                    "warnings": self._dedupe_text_list(warnings, 10),
                    "review_checklist": review_checklist or ["每个情景选择最贴近的一句，或在自填框里改成更像角色的话。"],
                }
            )
            parse_note = ""
            if fallback_reasons:
                parse_note = f"有 {len(fallback_reasons)} 批情景生成较慢，已先用本地候选补齐；不满意的情景可以单独重生成。"
            if not result.get("scenarios"):
                result = self._fallback_persona_style_scenarios_result(base_template, "模型返回空试答", specs=scenario_specs)
                parse_note = f"{parse_note} 模型返回试答为空，已生成本地兜底试答。".strip()
            return self._ok(
                {
                    "draft": result,
                    "provider_id": provider_id,
                    "provider_role": self._roleplay_provider_role(provider_id),
                    "repair_provider_id": repair_provider_id,
                    "repair_provider_role": self._roleplay_provider_role(repair_provider_id),
                    "parse_note": parse_note,
                    "raw_preview": self._single_line(raw_preview_source, 300),
                    "timeout_seconds": int(timeout_seconds),
                    "batch_size": batch_size,
                    "scenario_offset": scenario_offset,
                    "scenario_limit": scenario_limit,
                    "scenario_total": scenario_total,
                    "next_offset": next_offset,
                    "has_more": has_more,
                    "review_required": True,
                    "apply_supported": False,
                }
            )
        except Exception as exc:
            logger.error(f"人格风格试答生成失败: {exc}", exc_info=True)
            return self._exception_error("人格风格试答生成失败")

    async def retry_persona_style_scenario(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        base_template = self._multi_line(payload.get("base_template"), 12000)
        scenario = payload.get("scenario") if isinstance(payload.get("scenario"), dict) else {}
        feedback = self._multi_line(payload.get("feedback"), 1200)
        questionnaire = payload.get("questionnaire") if isinstance(payload.get("questionnaire"), dict) else {}
        try:
            if not base_template:
                return self._error("请先生成并确认基础设定稿")
            if not scenario:
                return self._error("缺少需要重生成的情景")
            caller = getattr(self.plugin, "_llm_call", None)
            if not callable(caller):
                return self._error("当前插件运行态无法调用模型")
            provider_id = self._standardize_persona_provider_id()
            system_prompt, user_prompt = self._persona_style_scenario_retry_prompt(base_template, scenario, feedback, questionnaire)
            raw = await caller(
                user_prompt,
                max_tokens=900,
                provider_id=provider_id,
                task="persona_style_scenario_retry",
                system_prompt=system_prompt,
            )
            parse_note = ""
            if raw is None:
                result = self._fallback_persona_style_scenario_retry_result(scenario, feedback, "模型调用返回空结果")
                parse_note = "模型调用未返回结果，已生成本地兜底候选。"
                raw_preview_source = ""
            else:
                raw_preview_source = raw
                try:
                    parsed = self._loads_json_object(raw)
                except Exception as parse_exc:
                    parsed = self._fallback_persona_style_scenario_retry_result(scenario, feedback, parse_exc)
                    parse_note = "模型未返回可解析 JSON，已生成本地兜底候选。"
                if isinstance(parsed, dict) and isinstance(parsed.get("scenarios"), list):
                    normalized = self._normalize_persona_style_scenarios_result(parsed)
                else:
                    normalized = self._normalize_persona_style_scenarios_result({"scenarios": [parsed]})
                result = normalized["scenarios"][0] if normalized.get("scenarios") else self._fallback_persona_style_scenario_retry_result(scenario, feedback, "模型返回空候选")
            return self._ok(
                {
                    "scenario": result,
                    "provider_id": provider_id,
                    "provider_role": self._roleplay_provider_role(provider_id),
                    "parse_note": parse_note,
                    "raw_preview": self._single_line(raw_preview_source, 240),
                    "review_required": True,
                    "apply_supported": False,
                }
            )
        except Exception as exc:
            logger.error(f"人格风格单情景重生成失败: {exc}", exc_info=True)
            return self._exception_error("人格风格单情景重生成失败")

    async def generate_persona_style_summary(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        base_template = self._multi_line(payload.get("base_template"), 12000)
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        questionnaire = payload.get("questionnaire") if isinstance(payload.get("questionnaire"), dict) else {}
        try:
            if not base_template:
                return self._error("请先生成并确认基础设定稿")
            if not evidence:
                return self._error("请先选择情景候选，或填写自定义回复/建议")
            caller = getattr(self.plugin, "_llm_call", None)
            if not callable(caller):
                return self._error("当前插件运行态无法调用模型")
            provider_id = self._standardize_persona_provider_id()
            system_prompt, user_prompt = self._persona_style_summary_prompt(base_template, evidence, questionnaire)
            raw = await caller(
                user_prompt,
                max_tokens=2600,
                provider_id=provider_id,
                task="persona_style_summary",
                system_prompt=system_prompt,
            )
            parse_note = ""
            if raw is None:
                result = self._fallback_persona_style_summary_result(evidence, "模型调用返回空结果")
                parse_note = "模型调用未返回结果，已生成本地兜底风格规则。"
                raw_preview_source = ""
            else:
                raw_preview_source = raw
                try:
                    parsed = self._loads_json_object(raw)
                except Exception as parse_exc:
                    parsed = self._fallback_persona_style_summary_result(evidence, parse_exc)
                    parse_note = "模型未返回可解析 JSON，已生成本地兜底风格规则。"
                result = self._normalize_persona_style_summary_result(parsed)
                if not str(result.get("style_block") or "").strip():
                    result = self._fallback_persona_style_summary_result(evidence, "模型返回空风格块")
                    parse_note = f"{parse_note} 模型返回风格块为空，已生成本地兜底风格规则。".strip()
            return self._ok(
                {
                    "draft": result,
                    "provider_id": provider_id,
                    "provider_role": self._roleplay_provider_role(provider_id),
                    "parse_note": parse_note,
                    "raw_preview": self._single_line(raw_preview_source, 240),
                    "review_required": True,
                    "apply_supported": False,
                }
            )
        except Exception as exc:
            logger.error(f"人格风格规则归纳失败: {exc}", exc_info=True)
            return self._exception_error("人格风格规则归纳失败")

    def _standardize_persona_provider_id(self) -> str:
        task_provider = getattr(self.plugin, "_task_provider", None)
        if callable(task_provider):
            return task_provider(
                getattr(self.plugin, "complex_reasoning_provider_id", ""),
                getattr(self.plugin, "fast_response_provider_id", ""),
                getattr(self.plugin, "llm_provider_id", ""),
            )
        return str(
            getattr(self.plugin, "complex_reasoning_provider_id", "")
            or getattr(self.plugin, "fast_response_provider_id", "")
            or getattr(self.plugin, "llm_provider_id", "")
            or ""
        ).strip()

    def _persona_style_scenarios_provider_id(self) -> str:
        task_provider = getattr(self.plugin, "_task_provider", None)
        if callable(task_provider):
            return task_provider(
                getattr(self.plugin, "fast_response_provider_id", ""),
                getattr(self.plugin, "complex_reasoning_provider_id", ""),
                getattr(self.plugin, "llm_provider_id", ""),
            )
        return str(
            getattr(self.plugin, "fast_response_provider_id", "")
            or getattr(self.plugin, "complex_reasoning_provider_id", "")
            or getattr(self.plugin, "llm_provider_id", "")
            or ""
        ).strip()
