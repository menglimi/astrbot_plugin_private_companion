# -*- coding: utf-8 -*-
"""Small asyncio fallback for AstrBot's bundled pytest environment."""
from __future__ import annotations

import asyncio
import importlib.util
import inspect

import pytest


_HAS_PYTEST_ASYNCIO = importlib.util.find_spec("pytest_asyncio") is not None


def pytest_configure(config):
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
