# -*- coding: utf-8 -*-
"""Expose this checkout under its canonical plugin package name."""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import inspect
import os
import sys
import types
from pathlib import Path
from typing import Iterator

import pytest


PACKAGE_NAME = "astrbot_plugin_private_companion"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))


def pytest_addoption(parser):
    parser.addoption(
        "--memory-plugin-root",
        action="store",
        default=None,
        metavar="PATH",
        help=(
            "path to a real memory companion checkout for cross-plugin integration "
            "contracts (also configurable with ASTRBOT_MEMORY_PLUGIN_ROOT)"
        ),
    )


@pytest.fixture(scope="session")
def memory_plugin_root(pytestconfig):
    """Return the real optional integration dependency, or skip its tests clearly."""
    from external_memory_dependency import resolve_memory_plugin_root

    resolution = resolve_memory_plugin_root(
        PLUGIN_ROOT,
        configured_root=pytestconfig.getoption("--memory-plugin-root"),
    )
    if resolution.root is None:
        pytest.skip(resolution.detail)
    return resolution.root


# Load the real package before legacy unit tests get a chance to install
# ``setdefault`` stubs into the shared full-suite process.  The explicit CI
# stub mode remains self-contained for artifact-import smoke tests.
if os.environ.get("ASTRBOT_CI_STUBS") != "1":
    try:
        import astrbot  # noqa: F401
    except ImportError:
        pass


if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__file__ = str(PLUGIN_ROOT / "__init__.py")
    package.__package__ = PACKAGE_NAME
    package.__path__ = [str(PLUGIN_ROOT)]
    spec = importlib.machinery.ModuleSpec(PACKAGE_NAME, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_ROOT)]
    package.__spec__ = spec
    sys.modules[PACKAGE_NAME] = package


if "astrbot" not in sys.modules:
    class _DummyMeta(type):
        def __getattr__(cls, _name):
            return cls

    class _Dummy(metaclass=_DummyMeta):
        def __new__(cls, *_args, **_kwargs):
            instance = super().__new__(cls)
            return instance

        def __init__(self, *_args, **_kwargs):
            pass

        def __call__(self, *_args, **_kwargs):
            return self

        def __getattr__(self, _name):
            return self

        def __iter__(self):
            return iter(())

        def __bool__(self):
            return False


    class _Logger:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None


    def _module(name: str, *, package: bool = False) -> types.ModuleType:
        value = types.ModuleType(name)
        if package:
            value.__path__ = []
        sys.modules[name] = value
        return value


    astrbot = _module("astrbot", package=True)
    api = _module("astrbot.api", package=True)
    api.logger = _Logger()
    api.AstrBotConfig = _Dummy
    astrbot.api = api

    api_event = _module("astrbot.api.event")
    api_event.MessageChain = _Dummy
    api_event.AstrMessageEvent = _Dummy
    api_event.MessageEventResult = _Dummy
    api_event.filter = _Dummy()
    api.event = api_event

    message_components = _module("astrbot.api.message_components")
    for _name in (
        "At",
        "BaseMessageComponent",
        "ComponentType",
        "File",
        "Image",
        "Plain",
        "Record",
        "Reply",
    ):
        setattr(message_components, _name, _Dummy)
    api.message_components = message_components

    api_provider = _module("astrbot.api.provider")
    api_provider.ProviderRequest = _Dummy
    api.provider = api_provider

    api_star = _module("astrbot.api.star")
    for _name in ("Context", "Star", "StarTools", "register"):
        setattr(api_star, _name, _Dummy)
    api.star = api_star

    core = _module("astrbot.core", package=True)
    core.file_token_service = _Dummy()
    astrbot.core = core

    main_agent = _module("astrbot.core.astr_main_agent")
    main_agent.MainAgentBuildConfig = _Dummy
    main_agent.build_main_agent = _Dummy()
    main_agent._apply_prompt_prefix = _Dummy()
    core.astr_main_agent = main_agent

    agent = _module("astrbot.core.agent", package=True)
    agent_message = _module("astrbot.core.agent.message")
    for _name in (
        "AssistantMessageSegment",
        "Message",
        "SystemMessageSegment",
        "TextPart",
        "UserMessageSegment",
    ):
        setattr(agent_message, _name, _Dummy)
    agent.message = agent_message
    agent_tool = _module("astrbot.core.agent.tool")
    agent_tool.FunctionTool = _Dummy
    agent_tool.ToolSet = _Dummy
    agent.tool = agent_tool
    agent_runners = _module("astrbot.core.agent.runners")
    agent.runners = agent_runners
    core.agent = agent

    pipeline = _module("astrbot.core.pipeline", package=True)
    core.pipeline = pipeline

    db = _module("astrbot.core.db", package=True)
    db_po = _module("astrbot.core.db.po")
    db_po.Conversation = _Dummy
    db.po = db_po
    core.db = db

    core_message = _module("astrbot.core.message", package=True)
    core_message_components = _module("astrbot.core.message.components")
    for _name in (
        "At",
        "BaseMessageComponent",
        "ComponentType",
        "Image",
        "Plain",
        "Record",
        "Reply",
    ):
        setattr(core_message_components, _name, _Dummy)
    core_message.components = core_message_components
    core_message_result = _module("astrbot.core.message.message_event_result")
    core_message_result.MessageEventResult = _Dummy
    core_message_result.ResultContentType = _Dummy
    core_message_result.MessageChain = _Dummy
    core_message.message_event_result = core_message_result
    core.message = core_message

    platform = _module("astrbot.core.platform", package=True)
    platform_message = _module("astrbot.core.platform.astrbot_message")
    platform_message.AstrBotMessage = _Dummy
    platform_message.MessageMember = _Dummy
    platform.astrbot_message = platform_message
    platform_session = _module("astrbot.core.platform.message_session")
    platform_session.MessageSession = _Dummy
    platform.message_session = platform_session
    platform_message_type = _module("astrbot.core.platform.message_type")
    platform_message_type.MessageType = _Dummy
    platform.message_type = platform_message_type
    platform_platform = _module("astrbot.core.platform.platform")
    platform_platform.PlatformStatus = _Dummy
    platform.platform = platform_platform
    platform_metadata = _module("astrbot.core.platform.platform_metadata")
    platform_metadata.PlatformMetadata = _Dummy
    platform.platform_metadata = platform_metadata
    platform_sources = _module("astrbot.core.platform.sources", package=True)
    platform.sources = platform_sources
    core.platform = platform

    star = _module("astrbot.core.star", package=True)
    star_handler = _module("astrbot.core.star.star_handler")
    star_handler.EventType = _Dummy
    star_handler.star_handlers_registry = _Dummy()
    star.star_handler = star_handler
    star_star = _module("astrbot.core.star.star")
    star_star.star_map = _Dummy()
    star.star = star_star
    star_filter = _module("astrbot.core.star.filter", package=True)
    star_filter_command = _module("astrbot.core.star.filter.command")
    star_filter_command.CommandFilter = _Dummy
    star_filter.command = star_filter_command
    star_filter_command_group = _module("astrbot.core.star.filter.command_group")
    star_filter_command_group.CommandGroupFilter = _Dummy
    star_filter.command_group = star_filter_command_group
    star_filter_event_type = _module("astrbot.core.star.filter.event_message_type")
    star_filter_event_type.EventMessageType = _Dummy
    star_filter_event_type.EventMessageTypeFilter = _Dummy
    star_filter.event_message_type = star_filter_event_type
    star.filter = star_filter
    core.star = star

    core_config = _module("astrbot.core.config", package=True)
    core_config_astrbot = _module("astrbot.core.config.astrbot_config")
    core_config_astrbot.AstrBotConfig = _Dummy
    core_config.astrbot_config = core_config_astrbot
    core_config_default = _module("astrbot.core.config.default")
    core_config_default.CONFIG_METADATA_2 = _Dummy()
    core_config.default = core_config_default
    core.config = core_config

    provider = _module("astrbot.core.provider", package=True)
    provider_entities = _module("astrbot.core.provider.entities")
    provider_entities.LLMResponse = _Dummy
    provider.entities = provider_entities
    core.provider = provider

    utils = _module("astrbot.core.utils", package=True)
    paths = _module("astrbot.core.utils.astrbot_path")
    paths.get_astrbot_data_path = lambda: Path(".")
    utils.astrbot_path = paths
    core.utils = utils

    quart = _module("quart")
    quart.request = _Dummy()
    quart.send_file = _Dummy()
    quart.Quart = _Dummy
    quart.Blueprint = _Dummy
    quart.jsonify = _Dummy()
    quart.Response = _Dummy
    quart.redirect = _Dummy()
    quart.url_for = _Dummy()
    quart.render_template = _Dummy()
    quart.current_app = _Dummy()
    quart.g = _Dummy()
    quart.session = _Dummy()
    quart.make_response = _Dummy()
    quart.abort = _Dummy()


_HAS_PYTEST_ASYNCIO = importlib.util.find_spec("pytest_asyncio") is not None
_MISSING = object()


def _restore_process_state(
    modules_before: dict[str, types.ModuleType | None],
    environ_before: dict[str, str],
) -> None:
    for name in tuple(sys.modules):
        if name not in modules_before:
            sys.modules.pop(name, None)
    for name, module in modules_before.items():
        if sys.modules.get(name, _MISSING) is not module:
            sys.modules[name] = module

    os.environ.clear()
    os.environ.update(environ_before)
    importlib.invalidate_caches()


@pytest.fixture(autouse=True)
def isolate_process_state() -> Iterator[None]:
    """Restore process-wide state changed by legacy import-style tests.

    A number of tests load modules under synthetic package names or temporarily
    replace AstrBot modules.  ``mock.patch.dict`` restores the keys it was given,
    but imported child modules and direct assignments otherwise survive into the
    next test.  Snapshot both module identities and the environment so every test
    starts from the same real AstrBot process state.
    """
    modules_before = dict(sys.modules)
    environ_before = dict(os.environ)
    yield

    _restore_process_state(modules_before, environ_before)


def pytest_runtest_teardown(item, nextitem):
    """Run after all fixture finalizers, including user monkeypatch fixtures."""
    snapshot = getattr(item, "_process_state_snapshot", None)
    if snapshot is not None:
        _restore_process_state(*snapshot)


def pytest_runtest_setup(item):
    item._process_state_snapshot = (dict(sys.modules), dict(os.environ))


def pytest_configure(config):
    configured_memory_root = config.getoption("--memory-plugin-root")
    if configured_memory_root:
        # Collection-time integration modules cannot consume fixtures, so expose
        # the command-line value through the same resolver input they use.
        os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"] = configured_memory_root
    config.addinivalue_line(
        "markers",
        "asyncio: run this coroutine test in an event loop",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run marked async tests when the optional pytest-asyncio plugin is absent."""
    if _HAS_PYTEST_ASYNCIO or "asyncio" not in pyfuncitem.keywords:
        return None
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None
    fixture_names = pyfuncitem._fixtureinfo.argnames
    kwargs = {name: pyfuncitem.funcargs[name] for name in fixture_names}
    asyncio.run(test_function(**kwargs))
    return True
