# -*- coding: utf-8 -*-
"""IntegrationStatusPart01Mixin。

由 tools/split_mixin_domain.py 从 integration_status.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 IntegrationStatusMixin）。
"""
from __future__ import annotations

from .integration_status_shared import logger
from .integration_status_shared import Any
from .integration_status_shared import DIAGNOSTIC_ENVELOPE_VERSION
from .integration_status_shared import DIAGNOSTIC_PUBLIC_FIELDS
from .integration_status_shared import PLUGIN_ID
from .integration_status_shared import Path
from .integration_status_shared import PromptRenderMode
from .integration_status_shared import PromptSection
from .integration_status_shared import _single_line
from .integration_status_shared import prompt_section
from .integration_status_shared import render_prompt_sections
from .integration_status_shared import runtime_persona_setting
from .integration_status_shared import sys



class IntegrationStatusPart01Mixin:
    """IntegrationStatusPart01Mixin（从 IntegrationStatusMixin 拆出）。"""


    @staticmethod
    def _diagnostic_operations_contract() -> dict[str, Any]:
        """Return the versioned public DTO contract used by operations."""
        return {
            "version": DIAGNOSTIC_ENVELOPE_VERSION,
            "fields": list(DIAGNOSTIC_PUBLIC_FIELDS),
            "history_projection": True,
        }

    def _patch_astrbot_plugin_page_asset_token_compat(self) -> None:
        """Give AstrBot plugin Pages a wider refresh window for this panel.

        AstrBot 4.26.x signs plugin Page static assets with a very short-lived
        asset_token. Some desktop/webview builds keep an iframe src around
        longer than that, so users see the raw JSON error before our page JS can
        refresh itself. The page assets are scoped static files; extending the
        window is a compatibility shim, not a permission bypass.
        """
        target_ttl_seconds = 6 * 60 * 60
        changed: list[str] = []
        try:
            from astrbot.dashboard.services import plugin_page_service as service_mod

            current = int(getattr(service_mod, "PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", 0) or 0)
            if 0 < current < target_ttl_seconds:
                setattr(service_mod, "PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", target_ttl_seconds)
                changed.append(f"service_ttl={current}->{target_ttl_seconds}")
        except Exception as exc:
            logger.debug("AstrBot 插件页 token TTL 兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.routes import plugin as legacy_route_mod

            current = int(getattr(legacy_route_mod, "_PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", 0) or 0)
            if 0 < current < target_ttl_seconds:
                setattr(legacy_route_mod, "_PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", target_ttl_seconds)
                changed.append(f"legacy_ttl={current}->{target_ttl_seconds}")
        except Exception as exc:
            logger.debug("AstrBot 旧插件页 token TTL 兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.plugin_page_auth import PluginPageAuth

            original = getattr(PluginPageAuth, "_private_companion_original_is_scope_valid", None)
            if original is None:
                original = PluginPageAuth.is_scope_valid
                setattr(PluginPageAuth, "_private_companion_original_is_scope_valid", original)

                def _is_scope_valid(cls, payload: dict, path: str) -> bool:
                    try:
                        if original(payload, path):
                            return True
                    except Exception:
                        return False
                    if not cls.is_protected_path(path) or path.startswith("/api/plugin/page/bridge-sdk.js"):
                        return False
                    token_plugin_name = payload.get("plugin_name")
                    token_page_name = payload.get("page_name")
                    request_plugin_name = cls.extract_plugin_name_from_path(path)
                    request_page_name = cls.extract_page_name_from_path(path)
                    page_aliases = {"陪伴面板", "companion-panel"}
                    return (
                        token_plugin_name == "astrbot_plugin_private_companion"
                        and request_plugin_name == "astrbot_plugin_private_companion"
                        and token_page_name in page_aliases
                        and request_page_name in page_aliases
                    )

                PluginPageAuth.is_scope_valid = classmethod(_is_scope_valid)
                changed.append("private_companion_page_alias_scope")
        except Exception as exc:
            logger.debug("AstrBot 插件页别名鉴权兼容补丁跳过: %s", _single_line(exc, 120))

        # A hot-reload can leave a StarMetadata object whose root_dir_name still
        # points at a temporary/old checkout.  The page and README routers use
        # that field directly, so a valid installed plugin can look missing.
        # Resolve only this plugin back to the directory of the currently loaded
        # module; other plugins keep AstrBot's normal path handling.
        companion_root = Path(__file__).resolve().parent

        def _is_companion_plugin(value: Any) -> bool:
            return str(getattr(value, "name", value) or "").strip() == PLUGIN_ID

        def _usable_companion_root() -> Path | None:
            if not companion_root.is_dir():
                return None
            if not (companion_root / "metadata.yaml").is_file():
                return None
            return companion_root

        try:
            from astrbot.dashboard.services import plugin_page_service as service_mod

            original_get_root = getattr(service_mod.PluginPageService, "get_plugin_root_dir", None)
            if callable(original_get_root) and not getattr(
                original_get_root, "_private_companion_root_fallback", False
            ):
                def _get_plugin_root_with_fallback(page_service, plugin):
                    try:
                        root = original_get_root(page_service, plugin)
                    except (FileNotFoundError, ValueError):
                        root = None
                    fallback = _usable_companion_root()
                    if _is_companion_plugin(plugin) and fallback is not None:
                        if root is None or not (root / "pages").is_dir():
                            return fallback
                    if root is None:
                        raise FileNotFoundError("Plugin directory metadata is missing")
                    return root

                _get_plugin_root_with_fallback._private_companion_root_fallback = True
                service_mod.PluginPageService.get_plugin_root_dir = _get_plugin_root_with_fallback
                changed.append("page_root_fallback")
        except Exception as exc:
            logger.debug("AstrBot 新插件页目录兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.routes import plugin as legacy_route_mod

            original_get_root = getattr(legacy_route_mod.PluginRoute, "_get_plugin_root_dir", None)
            if callable(original_get_root) and not getattr(
                original_get_root, "_private_companion_root_fallback", False
            ):
                def _get_plugin_root_with_fallback(route, plugin):
                    try:
                        root = original_get_root(route, plugin)
                    except (FileNotFoundError, ValueError):
                        root = None
                    fallback = _usable_companion_root()
                    if _is_companion_plugin(plugin) and fallback is not None:
                        if root is None or not (root / "pages").is_dir():
                            return fallback
                    if root is None:
                        raise FileNotFoundError("Plugin directory metadata is missing")
                    return root

                _get_plugin_root_with_fallback._private_companion_root_fallback = True
                legacy_route_mod.PluginRoute._get_plugin_root_dir = _get_plugin_root_with_fallback
                changed.append("legacy_page_root_fallback")

                original_logo_token = getattr(legacy_route_mod.PluginRoute, "get_plugin_logo_token", None)
                if callable(original_logo_token) and not getattr(
                    original_logo_token, "_private_companion_root_fallback", False
                ):
                    async def _get_logo_token_with_fallback(route, logo_path):
                        candidate = Path(str(logo_path or ""))
                        fallback = _usable_companion_root()
                        if (
                            fallback is not None
                            and candidate.name.lower() == "logo.png"
                            and not candidate.is_file()
                            and "private-companion" in str(candidate).lower()
                        ):
                            return await original_logo_token(route, str(fallback / "logo.png"))
                        return await original_logo_token(route, logo_path)

                    _get_logo_token_with_fallback._private_companion_root_fallback = True
                    legacy_route_mod.PluginRoute.get_plugin_logo_token = _get_logo_token_with_fallback
                    changed.append("legacy_logo_root_fallback")

                for method_name in ("get_plugin_readme", "get_plugin_changelog"):
                    original_method = getattr(legacy_route_mod.PluginRoute, method_name, None)
                    if not callable(original_method) or getattr(
                        original_method, "_private_companion_root_fallback", False
                    ):
                        continue

                    async def _read_document_with_fallback(route, _original=original_method, _kind=method_name):
                        result = await _original(route)
                        if not isinstance(result, dict) or result.get("status") != "error":
                            return result
                        try:
                            plugin_name = legacy_route_mod.request.args.get("name")
                        except Exception:
                            plugin_name = None
                        fallback = _usable_companion_root()
                        if str(plugin_name or "").strip() != PLUGIN_ID or fallback is None:
                            return result
                        candidates = (
                            ("README.md",)
                            if _kind == "get_plugin_readme"
                            else ("CHANGELOG.md", "changelog.md", "CHANGELOG", "changelog")
                        )
                        for filename in candidates:
                            path = fallback / filename
                            if not path.is_file():
                                continue
                            try:
                                content = path.read_text(encoding="utf-8")
                            except OSError:
                                return result
                            message = (
                                "成功获取README内容"
                                if _kind == "get_plugin_readme"
                                else "成功获取更新日志"
                            )
                            return legacy_route_mod.Response().ok(
                                {"content": content}, message
                            ).__dict__
                        return result

                    _read_document_with_fallback._private_companion_root_fallback = True
                    setattr(legacy_route_mod.PluginRoute, method_name, _read_document_with_fallback)
                changed.append("legacy_readme_root_fallback")
        except Exception as exc:
            logger.debug("AstrBot 旧插件页目录兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.services import plugin_service as service_mod

            original_resolve_dir = getattr(service_mod.PluginService, "resolve_plugin_dir", None)
            if callable(original_resolve_dir) and not getattr(
                original_resolve_dir, "_private_companion_root_fallback", False
            ):
                def _resolve_plugin_dir_with_fallback(plugin_service, plugin_name):
                    try:
                        return original_resolve_dir(plugin_service, plugin_name)
                    except Exception:
                        fallback = _usable_companion_root()
                        if str(plugin_name or "").strip() == PLUGIN_ID and fallback is not None:
                            return fallback
                        raise

                _resolve_plugin_dir_with_fallback._private_companion_root_fallback = True
                service_mod.PluginService.resolve_plugin_dir = _resolve_plugin_dir_with_fallback
                changed.append("readme_root_fallback")

            original_logo_url = getattr(service_mod.PluginService, "resolve_plugin_logo_url", None)
            if callable(original_logo_url) and not getattr(
                original_logo_url, "_private_companion_root_fallback", False
            ):
                async def _resolve_logo_url_with_fallback(plugin_service, plugin, logo_token_resolver):
                    fallback = _usable_companion_root()
                    logo_path = str(getattr(plugin, "logo_path", "") or "")
                    candidate = Path(logo_path)
                    if (
                        fallback is not None
                        and _is_companion_plugin(plugin)
                        and candidate.name.lower() == "logo.png"
                        and not candidate.is_file()
                    ):
                        logo_path = str(fallback / "logo.png")
                        token = await logo_token_resolver(logo_path)
                        return f"/api/file/{token}" if token else None
                    return await original_logo_url(plugin_service, plugin, logo_token_resolver)

                _resolve_logo_url_with_fallback._private_companion_root_fallback = True
                service_mod.PluginService.resolve_plugin_logo_url = _resolve_logo_url_with_fallback
                changed.append("logo_root_fallback")
        except Exception as exc:
            logger.debug("AstrBot README目录兼容补丁跳过: %s", _single_line(exc, 120))

        if changed:
            logger.info("AstrBot 插件页入口兼容已应用: %s", ", ".join(changed))

    def _register_page_api_if_available(self) -> None:
        try:
            from .page_api import PrivateCompanionPageApi

            self.page_api = PrivateCompanionPageApi(self)
        except Exception as e:
            logger.warning(f"插件拓展页面 API 初始化失败: {e}", exc_info=True)
            return

        if hasattr(self.context, "register_web_api"):
            try:
                self.page_api.register_routes()
                logger.info("插件拓展页面 API 已注册")
            except Exception as e:
                logger.warning(f"插件拓展页面 API 注册失败: {e}", exc_info=True)
        else:
            logger.debug("当前 AstrBot 版本未提供 register_web_api,跳过插件拓展页面 API 注册")

        try:
            from .standalone_webui import StandaloneWebUIServer

            self.standalone_webui = StandaloneWebUIServer(self, self.page_api)
        except Exception as e:
            self.standalone_webui = None
            logger.warning(f"独立陪伴 WebUI 初始化失败: {e}", exc_info=True)

    def _patch_livingmemory_processor_compat(self) -> None:
        """Work around LivingMemory versions whose MemoryProcessor lacks config."""
        if not self.enable_livingmemory_integration:
            return
        module = (
            sys.modules.get("data.plugins.astrbot_plugin_livingmemory.core.processors.memory_processor")
            or sys.modules.get("astrbot_plugin_livingmemory.core.processors.memory_processor")
        )
        if module is None:
            return
        processor_cls = getattr(module, "MemoryProcessor", None)
        if processor_cls is None or hasattr(processor_cls, "config"):
            return
        try:
            setattr(processor_cls, "config", {"atom_enabled": True})
            logger.info("已为 LivingMemory MemoryProcessor 添加 config 兼容兜底")
        except Exception as exc:
            logger.debug("LivingMemory 兼容补丁应用失败: %s", _single_line(exc, 120))

    def _integrated_plugin_installed(self, *names: str) -> bool:
        roots = [
            Path(__file__).resolve().parent.parent,
            Path(self.data_dir).parent.parent / "plugins",
        ]
        for root in roots:
            for name in names:
                try:
                    path = root / name
                    if (path / "main.py").exists() or (path / "metadata.yaml").exists():
                        return True
                except Exception:
                    continue
        return False

    def _report_integrated_feature_conflicts(self) -> None:
        rules = [
            (
                "enable_environment_perception",
                ("astrbot_plugin_llmperception", "astrbot_plugin_LLMPerception"),
                "检测到已安装 astrbot_plugin_LLMPerception,本插件内置环境感知仍按用户配置保持%s；如同时开启可能重复注入。",
            ),
            (
                "enable_group_scene_awareness",
                ("astrbot_plugin_context_aware",),
                "检测到已安装 astrbot_plugin_context_aware,本插件群聊场景感知仍按用户配置保持%s；如同时开启可能重复注入。",
            ),
            (
                "enable_atrelay_tools",
                ("astrbot_plugin_atrelay",),
                "检测到已安装 astrbot_plugin_atrelay,本插件跨群转述与 @ 群友工具仍按用户配置保持%s；如同时开启可能出现重复工具。",
            ),
        ]
        for key, plugin_names, message in rules:
            if self._integrated_plugin_installed(*plugin_names):
                state = "开启" if bool(getattr(self, key, False)) else "关闭"
                logger.info("%s", message % state)
        if self._integrated_plugin_installed("astrbot_plugin_proactive_chat"):
            enabled = bool(runtime_persona_setting(self, 'enable_proactive_chat_integration', True))
            review_mode = str(runtime_persona_setting(self, 'proactive_chat_bridge_review_mode', "local") or "local")
            logger.info(
                "已检测到 Proactive Chat，私聊主动发送联动%s（复核模式=%s）；不会修改对方调度配置。",
                "开启" if enabled else "关闭",
                review_mode,
            )

    def _worldview_mode_effective(self) -> str:
        mode = str(runtime_persona_setting(self, 'worldview_adaptation_mode', "auto") or "auto")
        if mode != "auto":
            return mode
        source = " ".join(
            part
            for part in (
                runtime_persona_setting(self, 'schedule_persona_prompt', ''),
                runtime_persona_setting(self, 'schedule_worldview_prompt', ''),
                runtime_persona_setting(self, 'worldview_adaptation_prompt', ''),
                runtime_persona_setting(self, 'bot_name', '小星'),
            )
            if part
        )
        if any(token in source for token in ("异世界", "冒险者", "魔法", "公会", "地下城", "勇者", "魔王", "骑士", "法师", "精灵", "龙")):
            return "fantasy"
        if any(token in source for token in ("赛博", "星舰", "宇宙", "殖民", "仿生", "义体", "AI 城市", "空间站")):
            return "sci_fi"
        return "modern"

    def _worldview_terms(self) -> dict[str, str]:
        mode = self._worldview_mode_effective()
        if mode == "fantasy":
            return {
                "mode": "fantasy",
                "life_log": "旅记/营地手札",
                "bookshelf": "行囊书匣",
                "secret_drawer": "暗格",
                "screen": "水晶映像",
                "video": "吟游诗人的影像记录",
                "group_chat": "酒馆/公会里的闲谈",
                "private_chat": "私信",
                "schedule": "旅途安排",
                "bored_watch": "翻看见闻记录",
                "reading_archive": "翻暗格里的藏书",
            }
        if mode == "sci_fi":
            return {
                "mode": "sci_fi",
                "life_log": "航行日志",
                "bookshelf": "私人资料柜",
                "secret_drawer": "加密夹层",
                "screen": "终端画面",
                "video": "影像流记录",
                "group_chat": "频道通信",
                "private_chat": "私密通信",
                "schedule": "今日流程",
                "bored_watch": "浏览影像流",
                "reading_archive": "翻加密藏本",
            }
        return {
            "mode": "modern",
            "life_log": "日记",
            "bookshelf": "资料柜",
            "secret_drawer": "夹层",
            "screen": "屏幕",
            "video": "B 站视频",
            "group_chat": "群聊",
            "private_chat": "私聊",
            "schedule": "日程",
            "bored_watch": "刷视频",
            "reading_archive": "翻资料柜夹层",
        }

    def _format_worldview_adaptation_prompt_body(
        self,
    ) -> str:
        mode = self._worldview_mode_effective()
        if mode == "off":
            return ""
        terms = self._worldview_terms()
        custom = _single_line(runtime_persona_setting(self, 'worldview_adaptation_prompt', ""), 1200)
        base = [
            f"当前适配模式：{mode}。",
            "插件功能名称只代表现实实现；最终表达必须服从当前人格、身份和世界观。",
            f"可把日程理解为：{terms['schedule']}；资料柜理解为：{terms['bookshelf']}；隐藏夹层理解为：{terms['secret_drawer']}；群聊理解为：{terms['group_chat']}；私聊理解为：{terms['private_chat']}。",
            f"外部或整合动作可转译为世界内等价行为：看视频≈{terms['bored_watch']}；识屏≈观察{terms['screen']}；资料归档≈{terms['reading_archive']}；空间动态≈公开生活札记/状态栏。",
            "如果人格/世界观与现代词冲突，优先使用世界内说法；但不要编造会改变功能结果的事实，也不要向用户解释后台实现。",
            "未在人格、世界观、关系网、近期对话或用户输入中明确出现的人际关系不得凭空添加；家人、父母、兄弟姐妹、亲戚、室友、同学、老师、同事、朋友、邻居、前辈、后辈等关系只能在材料有依据时使用。",
        ]
        if custom:
            base.append(f"自定义适配：{custom}")
        return "\n".join(base)

    def _format_worldview_adaptation_prompt(
        self,
        *,
        include_knowledge: bool = True,
    ) -> str:
        sections = (
            self._format_worldview_adaptation_prompt_sections()
            if include_knowledge
            else [self._format_worldview_adaptation_prompt_section()]
        )
        if not sections:
            return ""
        rendered = [
            render_prompt_sections(
                sections[:1],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        ]
        rendered.extend(
            render_prompt_sections(
                [section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            for section in sections[1:]
        )
        return "\n".join(part for part in rendered if part)

    def _format_worldview_adaptation_prompt_section(self) -> PromptSection:
        return prompt_section(
            key="worldview.adaptation",
            title="世界观适配",
            source="integration_status",
            content=self._format_worldview_adaptation_prompt_body(),
        )

    def _format_worldview_adaptation_prompt_sections(self) -> list[PromptSection]:
        section = self._format_worldview_adaptation_prompt_section()
        if not str(section.content or "").strip():
            return []
        sections = [section]
        knowledge_formatter = getattr(
            self,
            "_format_roleplay_knowledge_context_section",
            None,
        )
        if callable(knowledge_formatter):
            knowledge_section = knowledge_formatter(
                purpose="worldview",
                max_chars=1800,
                max_chunks=10,
            )
            if (
                isinstance(knowledge_section, PromptSection)
                and str(knowledge_section.content or "").strip()
            ):
                sections.append(knowledge_section)
        return sections

    def _livingmemory_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_livingmemory",
            Path(self.data_dir).parent / "astrbot_plugin_livingmemory",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_livingmemory",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]
