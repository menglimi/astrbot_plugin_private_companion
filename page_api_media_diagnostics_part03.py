# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaDiagnosticsPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_diagnostics.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaDiagnosticsMixin）。
"""
from __future__ import annotations

from .page_api_media_diagnostics_shared import logger
from .page_api_media_diagnostics_shared import Any
from .page_api_media_diagnostics_shared import Path
from .page_api_media_diagnostics_shared import PhotoReference
from .page_api_media_diagnostics_shared import _path_text
from .page_api_media_diagnostics_shared import asyncio
from .page_api_media_diagnostics_shared import json
from .page_api_media_diagnostics_shared import re
from .page_api_media_diagnostics_shared import time



class PrivateCompanionPageApiMediaDiagnosticsPart03Mixin:
    """PrivateCompanionPageApiMediaDiagnosticsPart03Mixin（从 PrivateCompanionPageApiMediaDiagnosticsMixin 拆出）。"""


    async def _run_image_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._image_api_runtime_lock():
            self._sync_photo_generation_runtime_config()
        called_plugin = self._image_generation_called_plugin_diagnostics()
        structured_generator = getattr(self.plugin, "_generate_photo_image_result", None)
        legacy_generator = getattr(self.plugin, "_generate_photo_image", None)
        generator = structured_generator if callable(structured_generator) else legacy_generator
        if not callable(generator):
            return {
                "ok": False,
                "title": "图片生成链路测试",
                "error": "插件缺少图片生成入口 _generate_photo_image",
                **called_plugin,
            }
        reference_enabled = bool(getattr(self.plugin, "enable_photo_reference_image", False))
        reference_getter = getattr(self.plugin, "_photo_persona_reference_image_path", None)
        reference_image_path = ""
        if reference_enabled and callable(reference_getter):
            try:
                reference_image_path = _path_text(reference_getter(), 1000)
            except Exception:
                reference_image_path = ""
        persona_reference = next(
            (
                item
                for item in (getattr(self.plugin, "photo_reference_catalog", ()) or ())
                if isinstance(item, PhotoReference) and item.kind == "persona"
            ),
            None,
        )
        configured_reference = _path_text(persona_reference.source if persona_reference is not None else "", 1000)
        has_reference_source = bool(reference_enabled and (reference_image_path or re.match(r"^https?://", configured_reference, flags=re.I)))
        workflow_kind = self._single_line(payload.get("workflow_kind"), 20)
        if not workflow_kind:
            workflow_kind = "selfie" if has_reference_source else "text2img"
        if workflow_kind in {"selfie", "portrait", "自拍", "人像"}:
            async_reference_getter = getattr(self.plugin, "_photo_persona_reference_image_for_kind_async", None)
            if callable(async_reference_getter):
                try:
                    reference_image_path = _path_text(
                        await async_reference_getter(workflow_kind, allow_daily_outfit=True),
                        1000,
                    )
                except Exception as exc:
                    logger.info(
                        "自拍排障参考图解析失败: %s",
                        self._single_line(exc, 160),
                    )
        prompt_text = self._single_line(payload.get("prompt"), 600)
        if not prompt_text and workflow_kind in {"selfie", "portrait", "自拍", "人像"}:
            if reference_image_path:
                prompt_text = (
                    "排障测试自拍图，保持参考图中的人物身份和外观一致，手机随手自拍构图，"
                    "自然室内光，画面干净清晰，真实摄影风格，不包含文字水印"
                )
            else:
                prompt_text = (
                    "排障测试自拍图，一名角色面向镜头，手机随手自拍构图，"
                    "自然室内光，画面干净清晰，真实摄影风格，不包含文字水印"
                )
        if not prompt_text:
            role_appearance = self._troubleshooting_role_appearance_prompt()
            if role_appearance:
                prompt_text = (
                    f"画面主体是这个角色：{role_appearance}。"
                    "角色面向镜头，手举一个简洁的小牌子，牌子上只有一个绿色对钩符号，"
                    "室内日常背景，画面干净清晰，构图自然，真实摄影或精致插画质感；"
                    "不要出现其他文字、水印、额外人物或变形手。"
                )
            else:
                prompt_text = (
                    "排障测试图，一枚小小的绿色对勾贴纸放在白色桌面上，旁边有柔和台灯光，"
                    "画面干净清晰，真实摄影风格，不包含人物、不包含文字水印"
                )
        started = time.time()
        diagnostics = self._image_generation_timeout_diagnostics(
            workflow_kind=workflow_kind,
            has_reference_source=has_reference_source,
            reference_image_path=reference_image_path,
        )
        diagnostics = {**called_plugin, **diagnostics}
        logger.info(
            "图片生成排障测试开始: workflow_kind=%s prompt_chars=%s reference=%s timeout=%ss estimated=%ss prompt=%s",
            self._single_line(workflow_kind, 40),
            len(str(prompt_text or "")),
            has_reference_source,
            diagnostics.get("test_timeout_seconds"),
            diagnostics.get("estimated_timeout_seconds"),
            self._single_line(prompt_text, 180),
        )
        timeout = self._int(diagnostics.get("test_timeout_seconds"), 240, 45, 900)
        try:
            generation_output = await asyncio.wait_for(
                generator(
                    workflow_kind=workflow_kind,
                    prompt_text=prompt_text,
                    session_key="private_companion_troubleshooting",
                    reference_image_path=reference_image_path,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            warnings = list(diagnostics.get("warnings") or [])
            timeout_targets = "在线 API、备用 API、参考图接口或本地工作流队列" if reference_image_path else "在线 API、备用 API 或本地工作流队列"
            warnings.insert(0, f"测试等待 {timeout}s 后仍未返回；实际链路可能卡在{timeout_targets}。")
            return {
                "ok": False,
                "title": "图片生成链路测试",
                "backend": "",
                "path": "",
                "file_size": 0,
                "detail": f"测试超时（{timeout}s）",
                "prompt": self._single_line(prompt_text, 220),
                "workflow_kind": self._single_line(workflow_kind, 20),
                "reference_image": _path_text(reference_image_path, 1000),
                "used_reference": False,
                "image_model": self._single_line(getattr(self.plugin, "external_image_api_model", ""), 80),
                "elapsed_ms": elapsed_ms,
                "error": f"测试超时（{timeout}s）",
                **diagnostics,
                "warnings": warnings[:8],
            }
        generation_metadata: dict[str, Any] = {}
        if hasattr(generation_output, "as_legacy_tuple"):
            backend_name, image_path, note = generation_output.as_legacy_tuple()
            generation_metadata = {
                "used_reference": bool(getattr(generation_output, "reference_used", False)),
                "reference_image": _path_text(getattr(generation_output, "reference_selected_path", ""), 1000),
                "reference_id": self._single_line(getattr(generation_output, "reference_id", ""), 60),
                "reference_kind": self._single_line(getattr(generation_output, "reference_kind", ""), 40),
                "reference_roles": list(getattr(generation_output, "reference_roles", ()) or ()),
                "wardrobe_mode": self._single_line(getattr(generation_output, "wardrobe_mode", ""), 40),
                "wardrobe_category": self._single_line(getattr(generation_output, "wardrobe_category", ""), 40),
                "outfit_locked": bool(getattr(generation_output, "outfit_locked", False)),
                "daily_outfit_removed": bool(getattr(generation_output, "daily_outfit_removed", False)),
                "final_presets": list(getattr(generation_output, "preset_names", ()) or ())[:1],
                "prompt_hash": self._single_line(getattr(generation_output, "prompt_hash", ""), 80),
                "prompt_path": _path_text(getattr(generation_output, "prompt_path", ""), 1000),
                "reference_requested_roles": list(getattr(generation_output, "reference_requested_roles", ()) or ()),
                "reference_excluded_roles": list(getattr(generation_output, "reference_excluded_roles", ()) or ()),
                "continuity_mode": self._single_line(getattr(generation_output, "continuity_mode", ""), 30),
                "reference_confidence": getattr(generation_output, "reference_confidence", 0.0),
                "reference_plan": list(getattr(generation_output, "reference_plan", ()) or ()),
                "reference_fulfilled_roles": list(getattr(generation_output, "reference_fulfilled_roles", ()) or ()),
                "reference_missing_roles": list(getattr(generation_output, "reference_missing_roles", ()) or ()),
                "reference_fallback_message": self._single_line(getattr(generation_output, "reference_fallback_message", ""), 260),
            }
        else:
            backend_name, image_path, note = generation_output
        elapsed_ms = int((time.time() - started) * 1000)
        exists = False
        file_size = 0
        if image_path:
            try:
                image_file = Path(str(image_path))
                exists = image_file.exists()
                file_size = image_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        logger.info(
            "图片生成排障测试结束: ok=%s backend=%s elapsed=%sms path=%s exists=%s size=%s note=%s",
            bool(image_path and exists),
            self._single_line(backend_name, 80),
            elapsed_ms,
            self._single_line(image_path, 180),
            exists,
            file_size,
            self._single_line(note, 180),
        )
        return {
            "ok": bool(image_path and exists),
            "title": "图片生成链路测试",
            "backend": self._single_line(backend_name, 80),
            "path": _path_text(image_path, 1000),
            "file_size": file_size,
            "detail": self._single_line(note, 220) or ("已生成图片" if image_path else "未返回图片路径"),
            "prompt": self._single_line(prompt_text, 220),
            "workflow_kind": self._single_line(workflow_kind, 20),
            "reference_image": _path_text(generation_metadata.get("reference_image") or reference_image_path, 1000),
            "used_reference": (
                bool(generation_metadata.get("used_reference"))
                if generation_metadata
                else self._image_generation_result_used_reference(
                    workflow_kind=workflow_kind,
                    image_path=image_path,
                    image_exists=exists,
                    note=note,
                )
            ),
            "reference_id": self._single_line(generation_metadata.get("reference_id"), 60),
            "reference_kind": self._single_line(generation_metadata.get("reference_kind"), 40),
            "reference_roles": list(generation_metadata.get("reference_roles") or [])[:8],
            "reference_intent": {
                "requested_roles": list(generation_metadata.get("reference_requested_roles") or [])[:8],
                "excluded_roles": list(generation_metadata.get("reference_excluded_roles") or [])[:8],
                "continuity_mode": self._single_line(generation_metadata.get("continuity_mode"), 30),
                "confidence": generation_metadata.get("reference_confidence", 0.0),
            },
            "reference_plan": list(generation_metadata.get("reference_plan") or [])[:8],
            "reference_fulfilled_roles": list(generation_metadata.get("reference_fulfilled_roles") or [])[:8],
            "reference_missing_roles": list(generation_metadata.get("reference_missing_roles") or [])[:8],
            "reference_fallback_message": self._single_line(generation_metadata.get("reference_fallback_message"), 260),
            "wardrobe_mode": self._single_line(generation_metadata.get("wardrobe_mode"), 40),
            "wardrobe_category": self._single_line(generation_metadata.get("wardrobe_category"), 40),
            "outfit_locked": bool(generation_metadata.get("outfit_locked")),
            "daily_outfit_removed": bool(generation_metadata.get("daily_outfit_removed")),
            "final_presets": list(generation_metadata.get("final_presets") or [])[:1],
            "prompt_hash": self._single_line(generation_metadata.get("prompt_hash"), 80),
            "prompt_path": _path_text(generation_metadata.get("prompt_path"), 1000),
            "image_model": self._single_line(getattr(self.plugin, "external_image_api_model", ""), 80),
            "elapsed_ms": elapsed_ms,
            "error": "" if image_path and exists else (self._single_line(note, 220) or "图片生成未返回有效文件"),
            **diagnostics,
        }

    def _image_generation_timeout_diagnostics(
        self,
        *,
        workflow_kind: str,
        has_reference_source: bool,
        reference_image_path: str,
    ) -> dict[str, Any]:
        preferred = self._single_line(getattr(self.plugin, "photo_generation_backend", "auto"), 30).lower() or "auto"
        external_timeout = self._int(getattr(self.plugin, "external_image_api_timeout_seconds", 180), 180, 20, 600)
        backup_timeout = self._int(getattr(self.plugin, "backup_external_image_api_timeout_seconds", 180), 180, 20, 600)
        comfyui_wait = self._int(getattr(self.plugin, "comfyui_photo_wait_seconds", 90), 90, 5, 600)
        endpoint_queue: list[dict[str, Any]] = []
        queue_getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
        if callable(queue_getter):
            try:
                endpoint_queue = [
                    endpoint
                    for endpoint in queue_getter(include_incomplete=True)
                    if isinstance(endpoint, dict) and endpoint.get("enabled", True)
                ]
            except Exception:
                endpoint_queue = []
        primary_external_configured = bool(
            getattr(self.plugin, "external_image_api_base_url", "")
            and getattr(self.plugin, "external_image_api_key", "")
            and getattr(self.plugin, "external_image_api_model", "")
        )
        endpoint_unavailable_note = getattr(
            self.plugin,
            "_external_image_api_endpoint_unavailable_note",
            None,
        )
        ready_endpoint_queue = []
        for endpoint in endpoint_queue:
            try:
                unavailable_note = (
                    endpoint_unavailable_note(endpoint)
                    if callable(endpoint_unavailable_note)
                    else "" if all(
                        str(endpoint.get(key) or "").strip()
                        for key in ("base_url", "api_key", "model")
                    ) else "incomplete"
                )
            except Exception:
                unavailable_note = ""
            if not unavailable_note:
                ready_endpoint_queue.append(endpoint)
        extension_status = self._image_generation_extension_status()
        generation_status = extension_status.get("generation")
        generation_status = generation_status if isinstance(generation_status, dict) else {}
        generation_state = self._single_line(generation_status.get("state"), 20).lower()
        generation_schema = self._single_line(
            generation_status.get("status_schema_version")
            or extension_status.get("status_schema_version"),
            80,
        )
        generation_backends = generation_status.get("backends")
        generation_backends = generation_backends if isinstance(generation_backends, dict) else {}
        has_explicit_generation_status = bool(
            generation_schema and generation_state in {"ready", "unavailable"}
        )
        if has_explicit_generation_status:
            external_available = bool(generation_backends.get("external"))
            backup_available = bool(generation_backends.get("backup_external"))
            comfyui_available = bool(generation_backends.get("comfyui"))
            sdgen_available = bool(generation_backends.get("sdgen"))
            availability_source = generation_schema
        else:
            reported_backup_available = bool(
                getattr(self.plugin, "_backup_external_photo_available", lambda: False)()
            )
            reported_external_available = bool(
                getattr(self.plugin, "_external_photo_available", lambda: False)()
            )
            external_available = bool(
                reported_external_available
                or ready_endpoint_queue
                or primary_external_configured
            )
            backup_available = bool(
                reported_backup_available
                or len(ready_endpoint_queue) > 1
            )
            comfyui_available = bool(getattr(self.plugin, "_comfyui_photo_available", lambda: False)())
            sdgen_available = bool(getattr(self.plugin, "_sdgen_photo_available", lambda: False)())
            if ready_endpoint_queue:
                availability_source = "endpoint_queue"
            elif primary_external_configured:
                availability_source = "legacy_config"
            else:
                availability_source = "legacy_runtime_probe"
        tool_call_timeout = self._image_generation_tool_call_timeout_seconds()

        segments: list[tuple[str, int]] = []
        warnings: list[str] = []
        if preferred == "nai":
            segments.append(("NAI 生图直连", external_timeout * 2))
            warnings.append("NAI 直连超时与重试由 NAI 生图插件内部控制，本插件只能估算耗时。")
        if preferred == "anima_master":
            segments.append(("Anima 绘图大师", 420))
            warnings.append("Anima 的生成、启动等待与重试由绘图大师配置控制，测试耗时为估算值。")
        if preferred in {"auto", "external"} and external_available:
            if ready_endpoint_queue:
                for index, endpoint in enumerate(ready_endpoint_queue[:12]):
                    timeout_seconds = self._int(endpoint.get("timeout_seconds"), external_timeout, 20, 600)
                    name = self._single_line(endpoint.get("name") or endpoint.get("model") or f"在线图片 API #{index + 1}", 40)
                    segments.append((name, timeout_seconds * 2))
                if len(ready_endpoint_queue) > 1:
                    warnings.append(
                        f"检测到 {len(ready_endpoint_queue)} 条可用在线生图 API：会按优先级逐条失败后再试下一条，完整失败链路会比单接口测试更慢。"
                    )
            else:
                segments.append(("主在线图片 API", external_timeout * 2))
                if backup_available:
                    segments.append(("备选在线图片 API", backup_timeout * 2))
                    warnings.append("已启用备选在线图片 API：主接口失败或超时后会再跑一轮备选接口，实际耗时可能明显长于单次测试观感。")
        if preferred in {"auto", "comfyui"} and comfyui_available:
            segments.append(("ComfyUI", comfyui_wait))
        if preferred in {"auto", "sdgen"} and sdgen_available:
            segments.append(("SDGen", 180))
            warnings.append("SDGen 调用由 SDGen 插件自身控制，本插件只能估算耗时，无法完全保证外层超时。")

        estimated = sum(seconds for _, seconds in segments) + 30
        if not segments:
            estimated = max(45, external_timeout + 30, comfyui_wait + 30)
        test_timeout = min(900, max(45, estimated + 30))
        if estimated + 30 > test_timeout:
            warnings.append(f"估算完整回退链路约 {estimated}s，排障测试外层最多等待 {test_timeout}s；极端慢链路仍可能被测试层截断。")
        if preferred == "auto" and len(segments) > 1:
            warnings.append("当前为自动后端：真实出图可能按在线 API、ComfyUI、SDGen 依次回退，测试通过只代表本次命中的那条链路可用。")
        if has_reference_source and workflow_kind not in {"selfie", "portrait", "自拍", "人像"}:
            warnings.append("已配置参考图，但本次测的是文生图；自拍、表情包、改图或 QQ 空间人物配图仍需单独测试参考图链路。")
        if workflow_kind in {"selfie", "portrait", "自拍", "人像"} and not reference_image_path and has_reference_source:
            warnings.append("检测到参考图配置，但本次没有解析到可用本地参考图；真实自拍可能会因下载/路径问题失败。")
        if external_available:
            warnings.append("在线图片 API 使用全局串行锁；多个生图请求同时发生时，后来的请求会先排队，单独点击测试无法覆盖排队等待。")
        if tool_call_timeout and estimated > tool_call_timeout:
            warnings.append(
                f"自然语言/主链工具调用可能受 AstrBot tool_call_timeout={tool_call_timeout}s 限制；"
                f"当前完整链路估算约 {estimated}s，测试能等到不代表 pc_generate_photo 工具一定不会超时。"
            )
        warnings.append("排障生图只检查生成文件，不覆盖后续发图、QQ 空间发布、记忆回写和主链工具调用耗时。")

        return {
            **self._image_generation_called_plugin_diagnostics(extension_status),
            "timeout_seconds": test_timeout,
            "test_timeout_seconds": test_timeout,
            "estimated_timeout_seconds": estimated,
            "timeout_budget": " + ".join(f"{name}{seconds}s" for name, seconds in segments) or "未命中可用后端",
            "backend_preference": preferred,
            "external_timeout_seconds": external_timeout,
            "backup_external_timeout_seconds": backup_timeout,
            "comfyui_wait_seconds": comfyui_wait,
            "backup_external": backup_available,
            "external_queue_lock": external_available,
            "availability_source": availability_source,
            "endpoint_count": len(endpoint_queue),
            "ready_endpoint_count": len(ready_endpoint_queue),
            "tool_call_timeout_seconds": tool_call_timeout,
            "warnings": warnings[:8],
        }

    def _image_generation_tool_call_timeout_seconds(self) -> int:
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return 120
        try:
            cfg = getter()
        except Exception:
            return 120
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(provider_settings, dict):
            return 120
        return self._int(provider_settings.get("tool_call_timeout"), 120, 1, 3600)

    def _photo_prompt_debug_payload(self, value: Any) -> dict[str, Any]:
        raw_path = _path_text(value, 1000)
        if not raw_path:
            return {}
        try:
            root = (Path(self.plugin.data_dir) / "photo_prompt_debug").resolve()
            path = Path(raw_path).expanduser().resolve()
            if path.parent != root or path.suffix.lower() != ".json" or not path.is_file():
                return {}
            if path.stat().st_size > 256 * 1024:
                return {}
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
