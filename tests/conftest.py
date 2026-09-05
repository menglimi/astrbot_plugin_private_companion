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


if os.environ.get("ASTRBOT_CI_STUBS") == "1" and "astrbot" not in sys.modules:
    class _Dummy:
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
    event = _module("astrbot.api.event")
    core = _module("astrbot.core", package=True)
    utils = _module("astrbot.core.utils", package=True)
    paths = _module("astrbot.core.utils.astrbot_path")
    api.logger = _Logger()
    event.MessageChain = _Dummy
    event.AstrMessageEvent = _Dummy
    event.filter = _Dummy()
    paths.get_astrbot_data_path = lambda: Path(".")
    astrbot.api = api
    core.utils = utils

    quart = _module("quart")
    quart.request = _Dummy()
    quart.send_file = _Dummy()


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
