"""Compatibility access to AstrBot host entry points that move between releases.

AstrBot 4.30 moves the public main-agent builder out of the old
``astrbot.core.astr_main_agent`` module.  The plugin must keep loading (and keep
its live extension optional) when a host removes the old path, so every import
that is not part of the stable ``astrbot.api`` surface goes through this module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _main_agent_api():
    """Return ``(MainAgentBuildConfig, build_main_agent)`` from the first host
    layout that provides the public builder.
    """
    attempts = (
        # AstrBot 4.30+ public runtime module.
        "astrbot.core.astr_agent_runtime.main_agent",
        # AstrBot 4.28-4.29 module, and the 4.3x package with the same API.
        "astrbot.core.astr_main_agent",
        # A package layout whose ``__init__`` does not re-export the builder.
        "astrbot.core.astr_main_agent.builder",
    )
    last_error: ImportError | None = None
    for module_name in attempts:
        try:
            module = __import__(module_name, fromlist=("MainAgentBuildConfig", "build_main_agent"))
            config, builder = module.MainAgentBuildConfig, module.build_main_agent
        except (ImportError, AttributeError) as error:
            last_error = error if isinstance(error, ImportError) else ImportError(str(error))
            continue
        if callable(builder):
            return config, builder
    raise ImportError("no AstrBot main-agent builder is available") from last_error


try:
    MainAgentBuildConfig, build_main_agent = _main_agent_api()
except ImportError:
    # Keep the rest of the plugin loadable.  Only the Bilibili live "auto reply
    # through a full main agent" path depends on this builder; it will report a
    # clear runtime warning instead of taking the whole plugin down at import.
    MainAgentBuildConfig = None
    build_main_agent = None
    logger.warning(
        "[CompanionV2] 当前 AstrBot 没有兼容的主 Agent 构建入口；B站直播自动回复将不可用。"
    )


def has_main_agent_builder() -> bool:
    return callable(build_main_agent)
