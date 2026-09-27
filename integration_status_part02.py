# -*- coding: utf-8 -*-
"""IntegrationStatusPart02Mixin。

由 tools/split_mixin_domain.py 从 integration_status.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 447 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 IntegrationStatusMixin）。
"""
from __future__ import annotations

from .integration_status_shared import _PLATFORM_DISPLAY_NAMES
from .integration_status_shared import Any
from .integration_status_shared import AstrMessageEvent
from .integration_status_shared import MessageType
from .integration_status_shared import Path
from .integration_status_shared import PromptRenderMode
from .integration_status_shared import PromptSection
from .integration_status_shared import _single_line
from .integration_status_shared import prompt_section
from .integration_status_shared import re
from .integration_status_shared import render_prompt_sections
from .integration_status_shared import runtime_persona_setting
from .integration_status_shared import sys
from .integration_status_shared import urlparse



class IntegrationStatusPart02Mixin:
    """IntegrationStatusPart02Mixin（从 IntegrationStatusMixin 拆出）。"""


    def _livingmemory_tool_available(self) -> bool | None:
        """Return tool availability when AstrBot exposes its runtime tool registry.

        ``None`` means the host is too old to expose a tool manager. A tool is
        usable only when it is active and its handler belongs to LivingMemory;
        a directory on disk is not evidence that the plugin is loaded.
        """
        tool_name = _single_line(getattr(self, "livingmemory_tool_name", ""), 60) or "recall_long_term_memory"
        context = getattr(self, "context", None)
        manager = None
        getter = getattr(context, "get_llm_tool_manager", None)
        if callable(getter):
            try:
                manager = getter()
            except Exception:
                manager = None
        if manager is None:
            provider_manager = getattr(context, "provider_manager", None)
            manager = getattr(provider_manager, "llm_tools", None)
        if manager is None:
            return None

        candidates: list[Any] = []
        get_func = getattr(manager, "get_func", None)
        if callable(get_func):
            try:
                tool = get_func(tool_name)
                if tool is not None:
                    candidates.append(tool)
            except Exception:
                pass
        try:
            candidates.extend(
                tool
                for tool in list(getattr(manager, "func_list", []) or [])
                if str(getattr(tool, "name", "") or "") == tool_name
            )
        except Exception:
            pass

        for tool in candidates:
            if not bool(getattr(tool, "active", True)):
                continue
            module_path = str(getattr(tool, "handler_module_path", "") or "")
            if not module_path:
                handler = getattr(tool, "handler", None)
                module_path = str(getattr(handler, "__module__", "") or "")
            if "livingmemory" in module_path.lower():
                return True
        return False

    @staticmethod
    def _livingmemory_module_loaded() -> bool:
        prefixes = (
            "astrbot_plugin_livingmemory",
            "data.plugins.astrbot_plugin_livingmemory",
        )
        return any(
            module is not None
            and any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
            for name, module in sys.modules.items()
        )

    def _livingmemory_available(self) -> bool:
        runtime_available = self._livingmemory_tool_available()
        if runtime_available is not None:
            return runtime_available
        # Compatibility fallback for old AstrBot versions without a tool manager.
        return self._livingmemory_module_loaded()

    def _format_livingmemory_guidance(
        self,
        *,
        scope: str = "private",
    ) -> str:
        sections = self._format_livingmemory_guidance_sections(scope=scope)
        if not sections:
            return ""
        guidance = render_prompt_sections(
            sections[:1],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        joke_boundary = render_prompt_sections(
            sections[1:],
            mode=PromptRenderMode.LABELED_INLINE,
        )
        return "\n".join(part for part in (guidance, joke_boundary) if part)

    @staticmethod
    def _livingmemory_guidance_parts(
        *,
        tool_name: str,
        boundary: str,
    ) -> tuple[str, str]:
        guidance = (
            f"上下文不够时可用 `{tool_name}` 查记忆。只有当前工具列表确实提供该工具时才调用；工具未出现时不要猜测、重试或输出工具调用。{boundary}结果只作接话背景。\n"
            "召回结果里出现人名、昵称、QQ 或群成员别名时,不要直接当作稳定身份；能查关系网时先用关系网确认,不能确认就按召回文本里的具体说话人原样转述。\n"
            "如果召回到 Bot 曾说自己在吃饭、整理、犯困、路上、创作等状态/日程，只能理解为当时 Bot 的拟人化表达或历史自称；不要写成用户事实、现实证据或持续状态。"
        )
        joke_boundary = (
            "群里“记住了/记下某人是XX”这类话（尤其把某人当对象、或带主观评价、攻击、贬损、色情、侮辱标签）通常只是群友之间的玩笑或随口一说。"
            "顺应玩笑节奏调侃接梗即可；这类玩笑记录可以另开为旁线补充，但不进入核心人物画像（主要用户画像、关系画像、稳定偏好），"
            "落到记忆中只能标为低置信的玩笑性质；只把可验证的客观事实当作主体画像内容。"
        )
        return guidance, joke_boundary

    def _format_livingmemory_guidance_sections(
        self,
        *,
        scope: str = "private",
    ) -> list[PromptSection]:
        if not self.enable_livingmemory_integration or not self._livingmemory_available():
            return []
        tool_name = _single_line(self.livingmemory_tool_name, 60) or "recall_long_term_memory"
        boundary = (
            "群聊只查当前群可公开使用的旧事。"
            if scope == "group"
            else "私聊可查当前用户相关的旧约定、偏好和共同经历。"
        )
        guidance, joke_boundary = self._livingmemory_guidance_parts(
            tool_name=tool_name,
            boundary=boundary,
        )
        return [
            prompt_section(
                key="livingmemory.guidance",
                title="长期记忆检索",
                source="livingmemory",
                content=guidance,
            ),
            prompt_section(
                key="livingmemory.group_joke_boundary",
                title="群聊玩笑边界",
                source="livingmemory",
                content=joke_boundary,
            ),
        ]

    def _format_livingmemory_status(self) -> str:
        # Check for "我会牢牢记住你" (RememberYou) bridge first
        companion_bridge = None
        try:
            companion_bridge = self._memory_companion_bridge()  # type: ignore[attr-defined]
        except Exception:
            pass
        if companion_bridge is not None:
            display_name = getattr(companion_bridge, "display_name", "") or "我会牢牢记住你"
            return (
                f"记忆插件协同：已检测到{display_name}。\n"
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
                f"情绪漂移联动：{'开启' if getattr(self, 'enable_memory_companion_emotional_drift', True) else '关闭'}\n"
                f"跨窗口情绪连续性：{'开启' if getattr(self, 'enable_memory_companion_cross_window_emotion', True) else '关闭'}\n"
                f"梦境碎片写入：{'开启' if getattr(self, 'enable_memory_companion_dream_fragment', True) else '关闭'}\n"
                f"未完成话题取材：{'开启' if getattr(self, 'enable_memory_companion_open_loop_search', True) else '关闭'}\n"
                f"功能上下文读取：{'开启' if getattr(self, 'enable_memory_companion_feature_context', True) else '关闭'}\n"
                "用途：长期记忆沉淀、短上下文补充、情绪漂移、梦境碎片和跨会话连续性。\n"
                "建议：保留本插件的生活状态/关系/群聊气氛层,把大规模长期检索交给记忆插件。"
            )
        companion_presence: dict[str, Any] = {}
        presence_getter = getattr(self, "_memory_companion_presence", None)
        if callable(presence_getter):
            try:
                candidate = presence_getter()
                if isinstance(candidate, dict):
                    companion_presence = candidate
            except Exception:
                companion_presence = {}
        if companion_presence.get("detected"):
            display_name = _single_line(companion_presence.get("display_name"), 80) or "我会牢牢记住你"
            version = _single_line(companion_presence.get("version"), 40) or "未知"
            plugin_path = _single_line(companion_presence.get("plugin_dir"), 260)
            reason = _single_line(companion_presence.get("reason"), 80)
            if reason == "bridge_disabled":
                detail = "陪伴插件侧的 MemoryCompanion Bridge 开关已关闭。"
            elif not companion_presence.get("activated", False):
                detail = "AstrBot 已发现插件，但插件当前未启用。"
            elif not companion_presence.get("loaded", False):
                detail = "已发现插件文件，但 AstrBot 尚未加载运行实例；可检查插件是否启用并在重载后刷新页面。"
            elif reason in {
                "capability_probe_missing",
                "capability_probe_exception",
                "capability_probe_invalid",
                "capability_contract_mismatch",
            }:
                detail = "插件已运行，但桥接能力协议未就绪或版本不兼容；请同步更新两个插件后重载。"
            elif reason == "optional_dependency_missing":
                detail = "插件已运行，但桥接所需的可选模型依赖缺失，当前已临时降级。"
            else:
                detail = "插件已检测到，但桥接实例暂未就绪；页面会自动重新探测。"
            lines = [
                f"记忆插件协同：已检测到{display_name}，但当前未建立可用桥接。",
                f"版本：{version}",
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}",
                detail,
            ]
            if plugin_path:
                lines.insert(2, f"路径：{plugin_path}")
            return "\n".join(lines)
        plugin_dir = self._livingmemory_plugin_dir()
        if not plugin_dir.exists():
            return (
                "记忆插件协同：未检测到\"我会牢牢记住你\"或 LivingMemory。\n"
                "当前会继续使用本插件内置的轻量记忆、片段记忆和群聊观察。"
            )
        metadata = plugin_dir / "metadata.yaml"
        version = ""
        if metadata.exists():
            try:
                text = metadata.read_text(encoding="utf-8")
                match = re.search(r"version:\s*([^\n]+)", text)
                if match:
                    version = match.group(1).strip()
            except Exception:
                version = ""
        if not self._livingmemory_available():
            return (
                "记忆插件协同：检测到 LivingMemory 文件，但插件未加载或召回工具未启用。\n"
                f"路径：{plugin_dir}\n"
                f"版本：{version or '未知'}\n"
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
                "当前不会注入 LivingMemory 召回提示；启用插件及其召回工具后会自动恢复。"
            )
        return (
            "记忆插件协同：已检测到 LivingMemory。\n"
            f"路径：{plugin_dir}\n"
            f"版本：{version or '未知'}\n"
            f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
            f"召回工具名：{self.livingmemory_tool_name or 'recall_long_term_memory'}\n"
            "用途：长期记忆、BM25/Faiss 混合检索、图谱记忆和 Agent 主动回忆。\n"
            "建议：保留本插件的生活状态/关系/群聊气氛层,把大规模长期检索交给 LivingMemory。"
        )

    def _llmperception_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_llmperception",
            Path(__file__).resolve().parent.parent / "astrbot_plugin_LLMPerception",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_llmperception",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_LLMPerception",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]

    def _llmperception_available(self) -> bool:
        try:
            return (self._llmperception_plugin_dir() / "main.py").exists()
        except Exception:
            return False

    def _context_aware_available(self) -> bool:
        return self._integrated_plugin_installed("astrbot_plugin_context_aware")

    def _atrelay_plugin_available(self) -> bool:
        return self._integrated_plugin_installed("astrbot_plugin_atrelay")

    def _platform_display_name(self, value: str) -> str:
        raw = _single_line(value, 40).lower()
        return _PLATFORM_DISPLAY_NAMES.get(raw, _single_line(value, 40) or self.target_platform or "未知平台")

    def _message_type_label(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "私聊"
        except Exception:
            pass
        try:
            if bool(getattr(event, "is_group_chat", lambda: False)()):
                return "群聊"
        except Exception:
            pass
        getter = getattr(event, "get_message_type", None)
        message_type = None
        if callable(getter):
            try:
                message_type = getter()
            except Exception:
                message_type = None
        friend_type = getattr(MessageType, "FRIEND_MESSAGE", None)
        group_type = getattr(MessageType, "GROUP_MESSAGE", None)
        if message_type == friend_type or str(message_type).lower().endswith("friend_message"):
            return "私聊"
        if message_type == group_type or str(message_type).lower().endswith("group_message"):
            return "群聊"
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if ":GroupMessage:" in umo:
            return "群聊"
        if ":FriendMessage:" in umo:
            return "私聊"
        return "当前会话"

    async def _format_platform_perception(self, event: AstrMessageEvent) -> str:
        if not runtime_persona_setting(self, 'enable_platform_perception', True):
            return ""
        platform = ""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                platform = _single_line(getter(), 40)
            except Exception:
                platform = ""
        if not platform:
            platform = str(getattr(event, "unified_msg_origin", "") or "").split(":")[0]
        if not platform:
            platform = self.target_platform or "aiocqhttp"
        parts = [self._platform_display_name(platform), self._message_type_label(event)]
        group_id = self._extract_group_id_from_event(event)
        group_name = ""
        if group_id:
            message_obj = getattr(event, "message_obj", None)
            group_obj = getattr(message_obj, "group", None) if message_obj is not None else None
            for attr in ("group_name", "name", "display_name"):
                value = getattr(group_obj, attr, None) if group_obj is not None else None
                if value:
                    group_name = _single_line(value, 50)
                    break
            get_group = getattr(event, "get_group", None)
            if not group_name and callable(get_group):
                try:
                    value = get_group(group_id=group_id)
                    if hasattr(value, "__await__"):
                        value = await value
                    group_name = _single_line(getattr(value, "group_name", "") or getattr(value, "name", ""), 50)
                except Exception:
                    group_name = ""
            parts.append(f"群号{group_id}" + (f"({group_name})" if group_name else ""))
        component_types: set[str] = set()
        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if class_name == "image":
                component_types.add("含图片")
            elif class_name in {"record", "voice", "audio"}:
                component_types.add("含语音")
            elif class_name == "video":
                component_types.add("含视频")
        parts.extend(sorted(component_types))
        return "、".join(part for part in parts if part)

    @staticmethod
    def _provider_config_value(provider: Any, *keys: str) -> str:
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        for key in keys:
            value = ""
            if isinstance(config, dict):
                value = str(config.get(key, "") or "")
            else:
                value = str(getattr(config, key, "") or "")
            if value.strip():
                return value.strip()
        return ""

    def _provider_identity_label(self, provider_id: str = "", provider: Any | None = None) -> str:
        safe_id = _single_line(provider_id, 120)
        if provider is None and safe_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(safe_id)
                except Exception:
                    provider = None
        if provider is None:
            return safe_id or "AstrBot 默认会话模型"
        name = self._provider_display_name(provider, safe_id)
        model = self._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        provider_id = safe_id or self._provider_config_value(provider, "id", "provider_id") or _single_line(getattr(provider, "provider_id", ""), 120)
        pieces = []
        if name:
            pieces.append(name)
        if model and model not in pieces:
            pieces.append(model)
        if provider_id and provider_id not in pieces:
            pieces.append(provider_id)
        return " / ".join(_single_line(piece, 120) for piece in pieces if piece) or "AstrBot 默认会话模型"

    @classmethod
    def _provider_display_name(cls, provider: Any, provider_id: str = "") -> str:
        explicit_name = (
            cls._provider_config_value(provider, "name", "display_name", "label", "title")
            or _single_line(getattr(provider, "name", ""), 80)
            or _single_line(getattr(provider, "display_name", ""), 80)
        )
        if explicit_name and not cls._provider_name_is_protocol(explicit_name):
            return explicit_name
        safe_id = _single_line(provider_id, 120)
        if safe_id and not cls._provider_name_is_protocol(safe_id):
            return safe_id
        inferred = cls._provider_vendor_from_config(provider)
        if inferred:
            return inferred
        protocol_name = cls._provider_config_value(provider, "provider", "type", "provider_type")
        return explicit_name or protocol_name or safe_id

    @staticmethod
    def _provider_name_is_protocol(value: Any) -> bool:
        text = str(value or "").strip().lower()
        normalized = re.sub(r"[\s_\-]+", "", text)
        return normalized in {
            "openai",
            "openai兼容",
            "openaicompatible",
            "compatible",
            "兼容",
            "兼容模式",
        }

    @classmethod
    def _provider_vendor_from_config(cls, provider: Any) -> str:
        source = " ".join(
            cls._provider_config_value(
                provider,
                "api_base",
                "base_url",
                "api_base_url",
                "api_url",
                "endpoint",
                "url",
                "model",
                "model_name",
                "api_model",
                "model_id",
            ).split()
        ).lower()
        if not source:
            return ""
        try:
            parsed = urlparse(source if "://" in source else f"https://{source}")
            host = parsed.netloc.lower()
        except Exception:
            host = ""
        haystack = f"{source} {host}"
        vendors = [
            ("火山引擎", ("volces.com", "volcengine", "huoshan", "火山", "doubao", "ark.cn-beijing")),
            ("DeepSeek", ("deepseek.com", "deepseek")),
            ("阿里云百炼", ("dashscope", "aliyuncs.com", "bailian", "百炼", "qwen", "tongyi")),
            ("OpenRouter", ("openrouter.ai", "openrouter")),
            ("硅基流动", ("siliconflow.cn", "siliconflow")),
            ("智谱", ("bigmodel.cn", "zhipu", "glm")),
            ("月之暗面", ("moonshot.cn", "moonshot", "kimi")),
            ("Google", ("generativelanguage.googleapis.com", "googleapis.com", "gemini")),
            ("Anthropic", ("anthropic.com", "claude")),
            ("OpenAI", ("api.openai.com", "openai.com", "gpt-")),
        ]
        for label, needles in vendors:
            if any(needle in haystack for needle in needles):
                return label
        if host:
            core = host.split(":")[0]
            for prefix in ("api.", "ark.", "www."):
                if core.startswith(prefix):
                    core = core[len(prefix):]
            return core.split(".")[0] or ""
        return ""

    def _provider_model_label(self, provider_id: str = "", provider: Any | None = None) -> str:
        safe_id = _single_line(provider_id, 120)
        if provider is None and safe_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(safe_id)
                except Exception:
                    provider = None
        if provider is None:
            return safe_id or "AstrBot 默认会话模型"
        model = self._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        return _single_line(model, 120) or safe_id or "AstrBot 默认会话模型"
